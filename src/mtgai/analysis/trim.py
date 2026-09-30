"""Cut a deck down to size — "it has to be 100: which ones go, and a few spares".

That question used to be answered by hand every time, with the tool's own
suggestions ignored half the time. This answers it with the tool's evidence,
in a fixed order of how strong each reason is:

1. **illegal** — outside the colour identity, banned, or a second copy.
2. **bracket** — only when the deck sits above its target bracket: surplus
   Game Changers, one piece of each combo that sets the bracket too high, mass
   land denial, chained extra turns. These are required, not suggested.
3. **declared** — what intent.md lists as flexible: Nicolas's own word that
   it can go.
4. **castability** — the mana base cannot cast it, and fixing the mana is not
   the better answer (engine.castability_cuts).
5. **oversupply** — a *dedicated* member of a cluster holding more cards than
   the deck needs, never below the cluster's floor.
6. **curve** — the most expensive spell outside the engine, while the deck is
   top-heavy.
7. **orphan** — a card with no job the tool can name.
8. **filler** — what does the least for the deck among what is left: first
   cards outside the engine with no support job, then support with room to
   spare, and only then engine pieces, least connected first. Never a way to
   win, never a support job below its minimum; engine picks are labelled a
   judgement call.

The first N picks are the cuts; the next few are extras, so there is room to
keep one of the cuts and cut something else instead. The commander, lands,
declared-sacred cards, combo pieces and the cards the declared win conditions
name are never touched — except that the bracket tier may break a combo that
holds the deck above its target, because that is what the target asks for.
"""

from __future__ import annotations

import copy
import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any, Callable

from .. import tags as tagmod
from ..model import CardEntry, Deck
from . import bracket as bracket_mod
from . import engine as engine_mod

DECK_SIZE = 100
# Same line as curve.py's "top-heavy" finding.
TOP_HEAVY = 12
EXPENSIVE_MV = 5

TIERS = (
    "illegal", "bracket", "declared", "castability", "oversupply", "curve", "orphan", "filler",
)


@dataclass(frozen=True)
class TrimPick:
    name: str
    tier: str
    reason: str
    evidence: str
    category: str | None = None
    # Popularity, when it only ordered candidates the deck's own evidence
    # already put forward. Recorded, never the reason.
    weak_signal: str | None = None
    mandatory: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Standing:
    """Where a card stands; `order` sorts the least needed first.

    kind 0 does nothing the deck is built around, kind 1 is support only,
    kind 2 feeds the engine. A job the owner named but the tool cannot
    place sorts after plain off-plan cards. Engine pieces go last, the
    least connected first — one core cluster before two — and within
    that the deepest cluster first: the fortieth counters card is spared
    more easily than the sixteenth sacrifice outlet.
    """
    kind: int
    links: set[str]
    core_links: set[str]
    depth: int = 0
    slack: int = 0
    job: str | None = None
    owner: tuple[str, ...] = ()

    @property
    def order(self) -> tuple:
        return (self.kind, bool(self.owner), len(self.core_links), -self.depth, -self.slack)


def _key(name: str) -> str:
    return name.split("//")[0].strip().lower()


def _named_in(lines: list[str], card: CardEntry) -> bool:
    """Whether a declared win-condition line names this card.

    Lines are free text ("Niv-Mizzet + Sheoldred infinite draw into Doctor
    Doom"), so the card's full front name or its short name before the comma
    counts, as a whole word, when at least four letters long.
    """
    front = card.name.split("//")[0].strip()
    names = {front, front.split(",")[0].strip()}
    text = " ".join(lines)
    for name in names:
        if len(name) >= 4 and re.search(rf"(?<![\w']){re.escape(name)}(?![\w'])", text, re.I):
            return True
    return False


class _State:
    """The deck as it stands after the picks so far."""

    def __init__(self, deck: Deck, result: dict[str, Any], wants: set[str], core: list[str],
                 tribe: str | None, overrides: dict[str, int]):
        self.deck = deck
        self.wants = wants
        self.core = core
        self.tribe = tribe
        self.remaining: Counter = Counter()
        for card in deck.cards:
            self.remaining[card.name] += card.quantity
        self.by_name = {c.name: c for c in deck.cards}
        self.picks: list[TrimPick] = []
        self.chosen: set[str] = set()
        self.counts: Counter = tagmod.deck_profile(deck)
        self.expensive = sum(
            c.quantity for c in deck.cards if not c.is_land and c.mana_value >= EXPENSIVE_MV
        )
        self.dedicated: dict[str, set[str]] = {}
        self.bounds: dict[str, tuple[int, int]] = {}
        for category in engine_mod.CATEGORY_TARGETS:
            self.bounds[category] = engine_mod.category_bounds(category, wants, overrides)
            self.dedicated[category] = {
                c.name
                for c in engine_mod.cards_in_category(deck, category)
                if engine_mod.is_dedicated(c, category, core, tribe)
            }

    def available(self, card: CardEntry) -> bool:
        return self.remaining[card.name] > 0 and card.name not in self.chosen

    def take(self, pick: TrimPick) -> None:
        card = self.by_name[pick.name]
        self.picks.append(pick)
        self.remaining[card.name] -= 1
        if self.remaining[card.name] <= 0:
            self.chosen.add(card.name)
        if not card.is_land:
            for category in tagmod.card_categories(card):
                self.counts[category] -= 1
            if card.mana_value >= EXPENSIVE_MV:
                self.expensive -= 1
        for members in self.dedicated.values():
            if self.remaining[card.name] <= 0:
                members.discard(card.name)

    def cuttable(self, category: str) -> int:
        floor, bound = self.bounds[category]
        return min(self.counts[category] - bound, len(self.dedicated[category]) - floor)


def plan_trim(
    deck: Deck,
    result: dict[str, Any],
    *,
    target: int = DECK_SIZE,
    extra: int = 3,
    max_bracket: int | None = None,
) -> dict[str, Any]:
    """The cuts that bring the deck to `target` cards, plus `extra` spares."""
    eng = result.get("engine") or {}
    intent = result.get("intent") or {}
    combos = (result.get("combos") or {}).get("complete") or []
    inclusion = (result.get("edhrec") or {}).get("inclusion") or {}
    bracket_result = result.get("bracket") or {}

    wants = set(eng.get("commander_wants") or [])
    tribe = eng.get("tribe")
    overrides = dict(intent.get("core_categories") or {})
    core = list(eng.get("core") or [])
    sacred = {_key(n) for n in intent.get("core_cards") or []}
    flexible = {_key(n) for n in intent.get("flexible_cards") or []}
    win_lines = list(intent.get("win_conditions") or [])

    if max_bracket is None:
        max_bracket = intent.get("power_bracket") or None
        source = "intent.md" if max_bracket else ""
        if not max_bracket and deck.archidekt_bracket:
            max_bracket, source = deck.archidekt_bracket, "Archidekt"
    else:
        source = "requested"

    total = deck.total_cards
    need = max(0, total - target)
    state = _State(deck, result, wants, core, tribe, overrides)

    combo_pieces = {name for combo in combos for name in combo.get("cards") or []}
    protected: dict[str, str] = {}
    for card in deck.cards:
        if card.is_commander:
            protected[card.name] = "commander"
        elif card.is_land:
            protected[card.name] = "land"
        elif _key(card.name) in sacred:
            protected[card.name] = "declared untouchable"
        elif card.name in combo_pieces or _key(card.name) in {_key(n) for n in combo_pieces}:
            protected[card.name] = "combo piece"
        elif win_lines and _named_in(win_lines, card):
            protected[card.name] = "named in the declared win conditions"
    no_data = set(eng.get("no_data") or [])

    def rank(cards: list[CardEntry]) -> list[CardEntry]:
        return engine_mod.rank_cut_candidates(cards, flexible=flexible, inclusion=inclusion)

    def weak(card: CardEntry) -> str | None:
        rate = inclusion.get(_key(card.name))
        if rate is None:
            return None
        return f"played in {rate:.0%} of decks with this commander"

    def free(card: CardEntry) -> bool:
        return state.available(card) and card.name not in protected

    # --- 1. illegal ---------------------------------------------------------
    identity = set(deck.color_identity())
    illegal: list[TrimPick] = []
    for card in sorted(deck.cards, key=lambda c: c.name):
        if card.is_commander:
            continue
        outside = set(card.color_identity) - identity
        if outside:
            illegal.append(TrimPick(card.name, "illegal",
                                    f"{'/'.join(sorted(outside))} is outside the commander's colour identity",
                                    "colour identity", mandatory=True))
        elif not card.commander_legal and not _unreleased(card):
            illegal.append(TrimPick(card.name, "illegal", "banned in Commander",
                                    "legality", mandatory=True))
        elif not card.is_basic_land and card.quantity > 1:
            for _ in range(card.quantity - 1):
                illegal.append(TrimPick(card.name, "illegal", "a second copy breaks singleton",
                                        "legality", mandatory=True))

    # --- 2. bracket ---------------------------------------------------------
    estimate = bracket_result.get("estimate")
    required: list[TrimPick] = []
    blocked: list[str] = []
    if max_bracket and estimate and estimate > max_bracket:
        required, blocked = _bracket_picks(deck, combos, max_bracket, protected, rank)

    for pick in illegal + required:
        if state.remaining[pick.name] > 0:
            state.take(pick)
    mandatory = len(state.picks)

    # --- 3-7: tiers that read the live state ------------------------------
    castability_cuts = sorted(
        eng.get("castability_cuts") or [], key=lambda f: (-f["severity"], f["name"])
    )
    # Live cuttable only shrinks as cuts are taken, so a category the
    # analysis already found nothing to trim in (or protected, as tutors in a
    # combo deck) never needs looking at.
    over_order = [
        e["category"] for e in eng.get("oversupplied") or [] if e.get("cuttable", 1) > 0
    ]
    wincons = {w["name"] for w in eng.get("win_conditions") or []}
    orphans = list(eng.get("orphans") or [])

    def at_floor(card: CardEntry, skip: str | None = None) -> bool:
        """Whether cutting the card takes a support job below its minimum —
        in cards doing only that job, the same line oversupply draws. It kept
        Sol Ring out of a list that had nothing better to cut, and a sweeper
        out of the removal surplus when it was one of two sweepers."""
        for category in tagmod.card_categories(card):
            if category not in state.bounds or category == skip:
                continue
            floor = state.bounds[category][0]
            if card.name in state.dedicated[category] and len(state.dedicated[category]) <= floor:
                return True
            if state.counts[category] - 1 < floor:
                return True
        return False

    def declared() -> TrimPick | None:
        cards = rank([c for c in deck.cards if _key(c.name) in flexible and free(c)])
        if not cards:
            return None
        return TrimPick(cards[0].name, "declared", "you marked it flexible in intent.md",
                        "declared flexible")

    def castability() -> TrimPick | None:
        for finding in castability_cuts:
            card = state.by_name.get(finding["name"])
            if card and free(card):
                why = "; ".join(engine_mod.cut_reasons(finding))
                if finding.get("sole_driver"):
                    why += " — nothing else asks this much of that colour"
                return TrimPick(card.name, "castability", why, "castability")
        return None

    def oversupply() -> TrimPick | None:
        # The category with the most to spare right now, so one surplus is
        # not emptied before a bigger one is touched.
        live = sorted(
            (c for c in over_order if c in state.bounds and state.cuttable(c) > 0),
            key=lambda c: (-state.cuttable(c), -(state.counts[c] - state.bounds[c][1]), c),
        )
        for category in live:
            members = [state.by_name[n] for n in sorted(state.dedicated[category])]
            candidates = rank([
                c for c in members
                if free(c) and not at_floor(c, skip=category)
                and not engine_mod.free_with_commander(c)
            ])
            if not candidates:
                continue
            card = candidates[0]
            floor, bound = state.bounds[category]
            reason = (
                f"{state.counts[category]} cards do {category} work and a deck wants about "
                f"{bound}; {len(state.dedicated[category])} of them feed nothing in the core "
                f"({', '.join(core)})"
            )
            return TrimPick(card.name, "oversupply", reason, f"oversupplied: {category}",
                            category=category, weak_signal=weak(card))
        return None

    def curve() -> TrimPick | None:
        if state.expensive <= TOP_HEAVY:
            return None
        candidates = [
            c for c in deck.cards
            if free(c) and not c.is_commander
            and engine_mod.effective_cost(c) >= EXPENSIVE_MV
            and not (set(c.engine_participation) & set(core))
            and c.name not in wincons and not at_floor(c)
        ]
        candidates.sort(key=lambda c: (-engine_mod.effective_cost(c), _key(c.name)))
        if not candidates:
            return None
        card = candidates[0]
        return TrimPick(
            card.name, "curve",
            f"{card.mana_value:.0f} mana, outside the engine, in a deck carrying "
            f"{state.expensive} spells at {EXPENSIVE_MV}+",
            "curve",
        )

    def orphan() -> TrimPick | None:
        cards = [state.by_name[n] for n in orphans if n in state.by_name]
        candidates = rank([c for c in cards if free(c)])
        if not candidates:
            return None
        card = candidates[0]
        return TrimPick(card.name, "orphan",
                        "no engine role and no support job the tool can name",
                        "no engine participation", weak_signal=weak(card))

    # What the owner filed five or more cards under is part of the plan too:
    # Deadpool's eleven "Copy" cards are the deck, whatever the commander's
    # own text asks for. Support categories stay support.
    owner_counts = Counter(
        category
        for card in deck.cards if not card.is_land
        for category in engine_mod.owner_categories(card)
    )
    owner_themes = {
        category for category, count in owner_counts.items()
        if count >= engine_mod.CLUSTER_THRESHOLD and category not in tagmod.SUPPORT
    }
    engine_cats = set(core) | wants | owner_themes
    support_cats = set(state.bounds) - engine_cats
    serving = {
        c.name for c in deck.cards if not c.is_land and engine_mod.serves_tribe(c, tribe)
    } if tribe else set()

    def cluster_size(category: str) -> int:
        # The tribe is counted by who serves it, not by typal tags alone.
        if category == "typal" and tribe:
            return sum(state.remaining[n] for n in serving)
        return state.counts[category]

    def owner_jobs(card: CardEntry) -> list[str]:
        """Owner categories naming a job the tool has no category for."""
        return [
            name for name in card.categories or []
            if tagmod.slugify(name) not in engine_mod._TYPE_CATEGORY_SLUGS
            and tagmod.slugify(name) not in engine_mod.OWNER_CATEGORY_MAP
        ]

    def standing(card: CardEntry) -> Standing:
        member = engine_mod.membership(card)
        links = member & engine_cats
        if card.name in serving:
            links = links | {"typal"}
        if links:
            core_links = links & set(core)
            depth = min((cluster_size(c) for c in core_links), default=0)
            return Standing(2, links, core_links, depth=depth)
        jobs = sorted(member & support_cats)
        if not jobs:
            return Standing(0, set(), set(), owner=tuple(owner_jobs(card)))

        def slack(job: str) -> int:
            floor = state.bounds[job][0]
            if card.name in state.dedicated[job]:
                return len(state.dedicated[job]) - floor
            return state.counts[job] - floor

        tightest = min(jobs, key=lambda j: (slack(j), j))
        return Standing(1, set(), set(), slack=slack(tightest), job=tightest)

    def filler() -> TrimPick | None:
        """What does the least for the deck among what is left.

        Never a way to win, never a card the tool knows nothing about, never
        a support job's last cards. Popularity only orders cards the deck's
        own evidence already ranks equal, and is recorded as such.
        """
        candidates = [
            card for card in deck.cards
            if not card.is_land and free(card) and card.name not in no_data
            and card.name not in wincons and not at_floor(card)
        ]
        if not candidates:
            return None
        stand = {card.name: standing(card) for card in candidates}

        def group(card: CardEntry) -> tuple:
            slugs = {tagmod.slugify(t) for t in card.tags or []}
            return (
                card.is_game_changer,
                bool(slugs & engine_mod._MULTIPLIER_TAGS),
                card.is_modal_land,
                stand[card.name].order,
            )

        def key(card: CardEntry) -> tuple:
            return (
                group(card),
                inclusion.get(_key(card.name), 1.0),
                -engine_mod.effective_cost(card),
                len(engine_mod.membership(card)),
                _key(card.name),
            )

        card = min(candidates, key=key)
        where = stand[card.name]
        peers = sum(1 for c in candidates if group(c) == group(card))
        engine_named = ", ".join(core)
        if where.kind == 0 and where.owner:
            reason = (
                f"you filed it under {', '.join(where.owner)}, which is not part of the "
                f"engine the tool reads ({engine_named})"
            )
            evidence = "judgement call: outside the engine"
        elif where.kind == 0:
            jobs = sorted(engine_mod.membership(card))
            reason = "does nothing this deck is built around" + (
                f" — {', '.join(jobs)} {'is' if len(jobs) == 1 else 'are'} outside the engine"
                if jobs else ""
            )
            evidence = "off-plan"
        elif where.kind == 1:
            job = where.job or ""
            floor = state.bounds[job][0]
            if card.name in state.dedicated[job]:
                room = f"{len(state.dedicated[job])} cards do nothing but {job}"
            else:
                room = f"{state.counts[job]} cards do {job} work"
            reason = f"support only, and {job} has room to spare: {room}, against a minimum of {floor}"
            evidence = f"spare {job}"
        elif where.core_links:
            reason = (
                f"of the core ({engine_named}) it only feeds "
                f"{', '.join(sorted(where.core_links))}, which {where.depth} cards already do"
            )
            evidence = "judgement call: least-connected engine piece"
        else:
            reason = (
                f"feeds nothing in the core ({engine_named}) — only "
                f"{', '.join(sorted(where.links))}, which the deck also leans on"
            )
            evidence = "judgement call: least-connected engine piece"
        # Among cards the deck's own evidence ranks equal, popularity picked
        # this one: said so in the weak signal, never in the reason.
        rate = inclusion.get(_key(card.name))
        signal = weak(card)
        if peers > 1 and where.kind != 0:
            if rate is not None:
                signal = f"the least played of the {peers} alike with this commander ({rate:.0%})"
            else:
                reason += f"; the most expensive of the {peers} alike"
        return TrimPick(card.name, "filler", reason, evidence, weak_signal=signal)

    tiers: list[Callable[[], TrimPick | None]] = [
        declared, castability, oversupply, curve, orphan, filler,
    ]
    wanted_total = max(need, mandatory) + max(0, extra)
    while len(state.picks) < wanted_total:
        pick = next((p for p in (tier() for tier in tiers) if p is not None), None)
        if pick is None:
            break
        state.take(pick)

    cut_count = max(need, mandatory)
    cuts = state.picks[:cut_count]
    extras = state.picks[cut_count:cut_count + max(0, extra)]

    after = _after(deck, cuts, combos, max_bracket, source)
    notes: list[str] = []
    if total <= target and not mandatory:
        notes.append(f"The deck has {total} cards — nothing needs to go to reach {target}.")
    if total < target:
        notes.append(f"It is {target - total} short of {target}.")
    below = mandatory > need
    if below:
        notes.append(
            f"Getting to bracket {max_bracket} takes {mandatory} cuts, more than the "
            f"{need} needed for {target} cards — add {mandatory - need} card"
            f"{'s' if mandatory - need > 1 else ''} that keep the bracket."
        )
    for message in blocked:
        notes.append(message)
    if len(cuts) < cut_count:
        notes.append(
            f"Only {len(cuts)} cuts have a reason the tool can stand behind; the other "
            f"{cut_count - len(cuts)} are your call."
        )
    first_call = next(
        (i for i, p in enumerate(cuts, 1) if p.evidence.startswith("judgement call")), None
    )
    if first_call:
        notes.append(
            f"From cut {first_call} on, nothing stronger is left: those picks are the "
            "least-connected cards — a judgement call, swap freely."
        )
        if not inclusion:
            notes.append("No EDHREC data, so those judgement calls are ordered by cost alone.")
    if not (result.get("combos") or {}).get("available", True):
        notes.append("Combos were not checked (offline), so combo pieces are not protected.")

    mana = result.get("mana") or {}
    land_note = None
    effective = mana.get("effective_lands", mana.get("land_count"))
    recommended = mana.get("recommended_lands")
    if effective is not None and recommended is not None and effective > recommended + 1:
        land_note = (
            f"{effective} land drops against ~{recommended} suggested — cutting a land "
            "instead of the last spell is reasonable."
        )

    return {
        "total": total,
        "target": target,
        "need": need,
        "extra": extra,
        "max_bracket": max_bracket,
        "bracket_source": source,
        "estimate": estimate,
        "cuts": [p.to_dict() for p in cuts],
        "extras": [p.to_dict() for p in extras],
        "required_for_bracket": [p.name for p in state.picks[:mandatory] if p.tier == "bracket"],
        "below_target": below,
        "protected": {
            name: why for name, why in sorted(protected.items())
            if why not in ("land", "commander")
        },
        "lands": {
            "count": mana.get("land_count"),
            "effective": effective,
            "recommended": recommended,
            "note": land_note,
        },
        "after": after,
        "notes": notes,
    }


def _unreleased(card: CardEntry) -> bool:
    from datetime import date

    return bool(card.released_at) and card.released_at[:10] > date.today().isoformat()


def _bracket_picks(
    deck: Deck,
    combos: list[dict[str, Any]],
    max_bracket: int,
    protected: dict[str, str],
    rank: Callable[[list[CardEntry]], list[CardEntry]],
) -> tuple[list[TrimPick], list[str]]:
    """The cuts the target bracket requires, and anything blocking them."""
    picks: list[TrimPick] = []
    blocked: list[str] = []
    taken: set[str] = set()

    def cuttable(card: CardEntry) -> bool:
        return protected.get(card.name) not in ("commander", "land", "declared untouchable")

    def add(card: CardEntry, reason: str, evidence: str) -> None:
        if card.name in taken:
            return
        taken.add(card.name)
        picks.append(TrimPick(card.name, "bracket", reason, evidence, mandatory=True))

    # Game Changers above what the target allows.
    allowance = 0 if max_bracket <= 2 else (
        bracket_mod.GAME_CHANGER_LIMIT_B3 if max_bracket == 3 else None
    )
    changers = [c for c in deck.cards if c.is_game_changer]
    if allowance is not None and len(changers) > allowance:
        for card in rank([c for c in changers if cuttable(c)])[: len(changers) - allowance]:
            add(card, f"a Game Changer; bracket {max_bracket} allows {allowance}", "bracket: Game Changer")
        stuck = [c.name for c in changers if not cuttable(c)]
        if stuck and len(changers) - len(stuck) < len(changers) - allowance:
            blocked.append(
                f"Bracket {max_bracket} also needs {', '.join(stuck)} out, which you declared untouchable."
            )

    # One piece of each combo that holds the deck above the target.
    raising = [
        c for c in combos
        if (bracket_mod.combo_floor(c) or 0) > max_bracket
    ]
    by_name = {c.name: c for c in deck.cards}
    for combo in sorted(raising, key=lambda c: str(c.get("id") or c.get("cards"))):
        pieces = [by_name[n] for n in combo.get("cards") or [] if n in by_name]
        if any(p.name in taken for p in pieces):
            continue
        options = [p for p in pieces if cuttable(p)]
        if not options:
            blocked.append(
                f"{' + '.join(combo.get('cards') or [])} keeps the deck above bracket "
                f"{max_bracket}, and every piece is untouchable."
            )
            continue
        shared = Counter(
            n for c in raising for n in c.get("cards") or [] if n in {p.name for p in options}
        )
        best = max(shared.values())
        ranked = rank([p for p in options if shared[p.name] == best])
        add(
            ranked[0],
            f"breaks {' + '.join(combo.get('cards') or [])}, a combo above bracket {max_bracket}",
            "bracket: combo",
        )

    if max_bracket < 4:
        for card in sorted(deck.cards, key=lambda c: c.name):
            if card.is_mass_land_denial and cuttable(card):
                add(card, "mass land denial is bracket 4", "bracket: mass land denial")
        turns = [c for c in deck.cards if c.is_extra_turns and cuttable(c)]
        if len(turns) > 1:
            for card in rank(turns)[: len(turns) - 1]:
                add(card, "more than one extra-turn card is chaining territory", "bracket: extra turns")
    return picks, blocked


def _after(
    deck: Deck,
    cuts: list[TrimPick],
    combos: list[dict[str, Any]],
    max_bracket: int | None,
    source: str,
) -> dict[str, Any]:
    """The deck as it would stand after the cuts."""
    trimmed = copy.deepcopy(deck)
    counts = Counter(p.name for p in cuts)
    kept = []
    for card in trimmed.cards:
        if counts[card.name]:
            card.quantity -= counts[card.name]
        if card.quantity > 0:
            kept.append(card)
    trimmed.cards = kept
    names = {c.name for c in kept}
    whole = [c for c in combos if all(n in names for n in c.get("cards") or [])]
    estimate = bracket_mod.analyse(trimmed, combos=whole, target=max_bracket, target_source=source)
    profile = tagmod.deck_profile(trimmed)
    return {
        "total": trimmed.total_cards,
        "bracket": estimate["estimate"],
        "bracket_name": estimate["name"],
        "categories": {
            category: {"count": profile.get(category, 0), "typical": list(bounds)}
            for category, bounds in engine_mod.CATEGORY_TARGETS.items()
        },
        "expensive_spells": sum(
            c.quantity for c in kept if not c.is_land and c.mana_value >= EXPENSIVE_MV
        ),
    }
