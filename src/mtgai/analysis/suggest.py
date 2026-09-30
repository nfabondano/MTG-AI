"""Structured suggestions: every idea carries its reason, source and group.

The old builder had one deck-internal path (cuts) and one popularity path
(adds). This one holds both to the same standard, and sorts every add into one
of three groups the report renders in order:

- **strengthens-plan** — ties directly to what the commander asks for: a combo
  one card away, fuel for the engine, a high-synergy card for *this build*.
- **fixes-weakness** — patches a measured structural gap (a role both counting
  systems agree is short, a mana-base shortfall).
- **meta-optional** — popularity with no deck-internal reason. Hidden by
  default; `--loose` shows it, clearly labelled. Popularity alone never
  outranks the deck's own evidence, per the project doctrine.
"""

from __future__ import annotations

from typing import Any

from .. import tags as tagmod
from ..model import Deck
from ..sources import scryfall, tagger
from . import engine as engine_mod

# What counts as the top of the curve: the same 5+ line as curve.py's
# "top-heavy" finding.
CURVE_TOP = 5

GROUP_PLAN = "strengthens-plan"
GROUP_WEAKNESS = "fixes-weakness"
GROUP_META = "meta-optional"

# Role shortage -> the Tagger tag whose cards fill it (ancestor rollup means
# the bare tag covers every specialised child).
ROLE_TAGS = {
    "removal": "removal",
    "draw": "draw",
    "ramp": "ramp",
    "wipe": "sweeper",
    "protection": "protection",
    "recursion": "recursion",
}

# How many candidates each internal need may put forward.
PER_NEED = 3
MAX_ADDS = 15


def build(
    deck: Deck,
    result: dict[str, Any],
    *,
    budget: float | None = None,
    loose: bool = False,
    offline: bool = False,
) -> dict[str, Any]:
    """Everything `suggest` has to say, structured; rendering happens later."""
    adds = build_adds(deck, result, budget=budget, loose=loose, offline=offline)
    cuts = build_cuts(deck, result, loose=loose)
    eng = result.get("engine") or {}
    return {
        "mana_fixes": eng.get("mana_fixes") or [],
        "adds": adds,
        "cuts": cuts,
        "swaps": pair_swaps(deck, adds, cuts),
        "hidden_meta": sum(1 for a in adds if a["group"] == GROUP_META and not loose),
    }


def _price_of(name: str) -> float | None:
    """Price from the local cache first, live Scryfall only as a fallback."""
    try:
        data = scryfall.by_name(name) or scryfall.lookup(name=name)
    except Exception:
        return None
    if not data:
        return None
    raw = (data.get("prices") or {}).get("usd")
    try:
        return float(raw) if raw else None
    except (TypeError, ValueError):
        return None


def _fits(card: dict[str, Any], identity: set[str], budget: float | None) -> bool:
    if not card:
        return False
    if (card.get("legalities") or {}).get("commander") != "legal":
        return False
    if not set(card.get("color_identity") or []) <= identity:
        return False
    if budget is not None:
        raw = (card.get("prices") or {}).get("usd")
        try:
            if raw and float(raw) > budget:
                return False
        except (TypeError, ValueError):
            return False
    return True


def internal_needs(deck: Deck, result: dict[str, Any]) -> list[dict[str, Any]]:
    """What the deck itself says it is missing — the commander's role and the
    reconciled role counts, never popularity."""
    needs: list[dict[str, Any]] = []
    eng = result.get("engine") or {}
    role = eng.get("commander_role") or {}
    census = eng.get("tribe_census") or {}
    tribe = eng.get("tribe")

    # A payoff commander over deaths runs on nontoken bodies of the tribe.
    if tribe and "payoff" in (role.get("roles") or []):
        fuel = census.get("true_type", 0) + census.get("changelings", 0)
        if fuel < 14:
            needs.append(
                {
                    "kind": "fuel",
                    "group": GROUP_PLAN,
                    "category": "typal",
                    "scryfall_query": f"type:{tribe.lower()} legal:commander",
                    "why": (
                        f"only {fuel} real {tribe}s feed the commander's trigger — "
                        f"the engine wants more bodies of the tribe"
                    ),
                    "evidence": "tribe census",
                }
            )

    # Role shortages that survived reconciliation are agreed by both counters.
    for finding in (result.get("roles") or {}).get("findings") or []:
        if finding.get("verdict") != "low":
            continue
        role_name = finding["role"]
        if role_name not in ROLE_TAGS:
            continue
        low, high = finding["target"]
        needs.append(
            {
                "kind": "role",
                "group": GROUP_WEAKNESS,
                "category": ROLE_TAGS[role_name],
                "tagger_tag": ROLE_TAGS[role_name],
                "why": (
                    f"{finding['count']} {role_name} against a usual {low}-{high} — "
                    f"both counting systems agree it is short"
                ),
                "evidence": "role shortage",
            }
        )
    return needs


def _candidates_for(
    need: dict[str, Any],
    deck: Deck,
    identity: set[str],
    *,
    budget: float | None,
    offline: bool,
) -> list[dict[str, Any]]:
    """Concrete cards for one need: the Tagger index joined to the local
    Scryfall cache, or a Scryfall search when the need names one."""
    in_deck = deck.names()
    found: list[dict[str, Any]] = []

    if need.get("tagger_tag"):
        for oracle_id in tagger.cards_with_tag(need["tagger_tag"]):
            card = scryfall.by_oracle_id(oracle_id)
            if not card or card["name"].split("//")[0].strip().lower() in in_deck:
                continue
            if _fits(card, identity, budget):
                found.append(card)

    elif need.get("scryfall_query") and not offline:
        colours = "".join(sorted(identity)) or "c"
        query = f"{need['scryfall_query']} id<={colours}"
        for card in scryfall.search(query, max_pages=1):
            if card["name"].split("//")[0].strip().lower() in in_deck:
                continue
            if _fits(card, identity, budget):
                found.append(card)

    found.sort(key=lambda c: c.get("edhrec_rank") or 10**9)
    return found[:PER_NEED]


def build_adds(
    deck: Deck,
    result: dict[str, Any],
    *,
    budget: float | None = None,
    loose: bool = False,
    offline: bool = False,
) -> list[dict[str, Any]]:
    adds: list[dict[str, Any]] = []
    seen: set[str] = set(deck.names())
    identity = set(deck.color_identity())

    def add(entry: dict[str, Any]) -> None:
        key = entry["name"].split("//")[0].strip().lower()
        if key in seen:
            return
        seen.add(key)
        adds.append(entry)

    # Combos one card away are the most concrete change available — unless
    # Spellbook marks the combo as using a banned card.
    near_misses = [
        c for c in result.get("combos", {}).get("near_miss") or []
        if c.get("bracket_tag") != "B"
    ]
    for combo in near_misses[:5]:
        missing = combo.get("missing")
        if not missing:
            continue
        price = _price_of(missing)
        if budget is not None and price is not None and price > budget:
            continue
        have = " + ".join(c for c in combo["cards"] if c != missing)
        produces = ", ".join(combo["produces"][:2])
        add(
            {
                "name": missing,
                "price": price,
                "group": GROUP_PLAN,
                "source": "combo",
                "category": None,
                "why": f"completes a combo with {have} → {produces}",
                "evidence": "Commander Spellbook",
            }
        )

    # What the deck itself is short of.
    for need in internal_needs(deck, result):
        for card in _candidates_for(need, deck, identity, budget=budget, offline=offline):
            raw_price = (card.get("prices") or {}).get("usd")
            try:
                price = float(raw_price) if raw_price else None
            except (TypeError, ValueError):
                price = None
            add(
                {
                    "name": card["name"],
                    "price": price,
                    "group": need["group"],
                    "source": "deck-internal",
                    "category": need.get("category"),
                    "why": need["why"],
                    "evidence": need["evidence"],
                }
            )

    # EDHREC: synergy is commander-specific signal and joins the plan group,
    # labelled with the population it was measured against. Raw inclusion is
    # popularity and stays meta-optional.
    edh = result.get("edhrec") or {}
    basis = edh.get("basis_label") or "vs all builds of this commander"
    for entry in edh.get("missing_synergy") or []:
        price = _price_of(entry["name"])
        if budget is not None and price is not None and price > budget:
            continue
        add(
            {
                "name": entry["name"],
                "price": price,
                "group": GROUP_PLAN,
                "source": "edhrec-synergy",
                "category": None,
                "why": f"synergy +{entry.get('synergy', 0):.2f} measured {basis}",
                "evidence": "EDHREC synergy",
            }
        )
        if len(adds) >= MAX_ADDS:
            break

    for entry in edh.get("missing_staples") or []:
        if len(adds) >= MAX_ADDS + 5:
            break
        price = _price_of(entry["name"])
        if budget is not None and price is not None and price > budget:
            continue
        add(
            {
                "name": entry["name"],
                "price": price,
                "group": GROUP_META,
                "source": "edhrec-staple",
                "category": None,
                "why": f"played in {entry.get('inclusion', 0):.0%} of decks — popularity only",
                "evidence": "inclusion rate",
            }
        )

    order = {GROUP_PLAN: 0, GROUP_WEAKNESS: 1, GROUP_META: 2}
    adds.sort(key=lambda a: order.get(a["group"], 3))
    return adds


def build_cuts(
    deck: Deck, result: dict[str, Any], *, loose: bool = False
) -> list[dict[str, Any]]:
    """Cut candidates, each backed by deck-internal evidence.

    Popularity is deliberately not evidence. The tool once proposed cutting an
    entire aristocrats engine because those cards sat outside a truncated
    EDHREC list, so a card's absence from other people's decks can no longer
    justify anything on its own.

    What does justify a cut, in order of how much it tells you:

    1. **Castability** — the card asks for more coloured mana than the deck
       makes, and fixing the mana instead would not be better (sole drivers
       and three-colour costs; strained engine pieces become mana fixes).
    2. **Oversupply** — the card sits in a cluster holding far more than the
       deck needs, and serves neither a core cluster nor the tribe.
    3. **Curve** — a top-of-curve card in a deck that is already top-heavy.
    4. **No engine participation** — it does nothing the deck is built around.

    A card outside the colour identity is not a candidate but a requirement,
    and a card declared sacred in intent.md is refused everywhere else.
    """
    cuts: list[dict[str, Any]] = []
    seen: set[str] = set()
    eng = result.get("engine") or {}
    intent_data = result.get("intent") or {}
    sacred = {
        name.split("//")[0].strip().lower()
        for name in intent_data.get("core_cards") or []
    }
    flexible = {
        name.split("//")[0].strip().lower()
        for name in intent_data.get("flexible_cards") or []
    }

    def add(name: str, why: str, evidence: str, score: int, **extra: Any) -> bool:
        key = name.split("//")[0].strip().lower()
        if key in seen:
            return False
        # Declared untouchable. Colour-identity violations are the one
        # exception: an illegal card is a fact, not a suggestion.
        if key in sacred and evidence != "colour identity":
            return False
        seen.add(key)
        cuts.append({"name": name, "why": why, "evidence": evidence, "score": score, **extra})
        return True

    # Illegal cards are not suggestions.
    identity = set(deck.color_identity())
    for card in deck.cards:
        if card.is_commander:
            continue
        outside = set(card.color_identity) - identity
        if outside:
            add(
                card.name,
                f"illegal — {'/'.join(sorted(outside))} is outside your colour identity",
                "colour identity",
                1000,
            )

    protected = _combo_cards(result)

    # Never offer the card that would take a support job below its minimum —
    # one of two sweepers is not spare removal, and not "top of the curve".
    profile = tagmod.deck_profile(deck)
    wants = eng.get("commander_wants") or []
    overrides = dict(intent_data.get("core_categories") or {})

    def below_floor(card, skip: str | None = None) -> bool:
        return any(
            profile.get(category, 0) - 1
            < engine_mod.category_bounds(category, wants, overrides)[0]
            for category in tagmod.card_categories(card)
            if category in engine_mod.CATEGORY_TARGETS and category != skip
        )

    # 1. Castability.
    for entry in (eng.get("castability_cuts") or [])[:6]:
        if entry["name"] in protected:
            continue
        why = "; ".join(engine_mod.cut_reasons(entry))
        if entry.get("sole_driver"):
            why += " — and nothing else in the deck asks this much of that colour, so cutting it relaxes the whole mana base"
        if entry.get("alternative"):
            why += f" (or {entry['alternative']})"
        add(entry["name"], why, "castability", 100 + entry.get("severity", 0))

    # 2. Oversupply — name the specific cards making up the excess. Only the
    #    cluster's *dedicated* members are candidates (engine_mod.oversupplied
    #    works them out): a card that also serves a core cluster, or is the
    #    tribe, is doing double duty, and offering it would repeat the Ashnod's
    #    Altar mistake. A cluster whose excess is all engine pieces has nothing
    #    to trim and is skipped.
    inclusion = (result.get("edhrec") or {}).get("inclusion") or {}
    shown = 0
    for over in eng.get("oversupplied") or []:
        if shown >= 3:
            break
        if over.get("cuttable", 1) <= 0:
            continue
        category = over["category"]
        members = [deck.find(n) for n in over.get("members_dedicated") or []]
        candidates = [
            c for c in members
            if c is not None and c.name not in protected
            and c.name.split("//")[0].strip().lower() not in seen
            and c.name.split("//")[0].strip().lower() not in sacred
            and not below_floor(c, skip=category)
        ]
        # A spell that is free with the commander out is never the one to
        # cut from a surplus: it costs the deck nothing to keep.
        ranked = [
            c for c in engine_mod.rank_cut_candidates(
                candidates, flexible=flexible, inclusion=inclusion
            )
            if not engine_mod.free_with_commander(c)
        ]
        if not ranked:
            continue
        first = ranked[0]
        dedicated = over.get("dedicated", over["count"])
        why = (
            f"{over['count']} cards do {category} work"
            + (
                f", {dedicated} of them feeding nothing in the core"
                if dedicated != over["count"] else ""
            )
            + f"; a deck wants about {over['target_high']}"
        )
        extra: dict[str, Any] = {
            "alternatives": [c.name for c in ranked[1:] if not engine_mod.last_resort(c)][:3]
        }
        rate = inclusion.get(first.name.split("//")[0].strip().lower())
        if rate is not None:
            # Popularity orders candidates the deck already put forward; it is
            # recorded as such and never becomes the reason.
            extra["weak_signal"] = f"played in {rate:.0%} of decks with this commander"
        if add(first.name, why, f"oversupplied: {category}", 60, **extra):
            shown += 1

    # 3. Curve.
    curve = result.get("curve") or {}
    if curve.get("expensive_spells", 0) > 12:
        wincons = {w["name"] for w in eng.get("win_conditions") or []}
        core = set(eng.get("core") or [])

        # The same cards trail here as in every other ranking: Game
        # Changers, spell-lands, what multiplies the deck's mechanic, free
        # spells. Among the rest, what feeds fewer of the core clusters goes
        # before what feeds more — a 7-drop Vampire in the Vampire deck is
        # the last expensive card to go, not the first. A way to win is
        # never "top of the curve".
        top = sorted(
            (
                c for c in deck.cards
                if not c.is_land and not c.is_commander and c.name not in wincons
                and engine_mod.effective_cost(c) >= CURVE_TOP and not below_floor(c)
            ),
            key=lambda c: (
                engine_mod.last_resort(c),
                len(set(c.engine_participation) & core),
                -engine_mod.effective_cost(c),
                c.name,
            ),
        )
        offered = 0
        for card in top:
            if offered >= 2:
                break
            if card.name in protected:
                continue
            if add(
                card.name,
                f"{card.mana_value:.0f} mana in a deck already carrying "
                f"{curve['expensive_spells']} spells at 5+",
                "curve",
                50,
            ):
                offered += 1

    # 4. Does nothing the deck is built around.
    for name in (eng.get("orphans") or [])[:5]:
        if name in protected:
            continue
        add(
            name,
            f"outside every cluster this deck is built on ({eng.get('archetype', 'its theme')})",
            "no engine participation",
            30,
        )

    # 5. Measured-low inclusion, and only ever as corroboration.
    if loose:
        for entry in (result.get("edhrec") or {}).get("off_meta") or []:
            if entry["name"] in protected:
                continue
            add(entry["name"], entry["reason"], "low inclusion (weak signal)", 10)

    cuts.sort(key=lambda c: -c["score"])
    return cuts


def _combo_cards(result: dict[str, Any]) -> set[str]:
    """Cards that assemble a combo the deck already has — never offer these."""
    protected: set[str] = set()
    for combo in result.get("combos", {}).get("complete") or []:
        protected.update(combo.get("cards") or [])
    return protected


def pair_swaps(
    deck: Deck, adds: list[dict[str, Any]], cuts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Pair a cut with an add doing the same job, or leave it unpaired.

    The old positional zip produced pairs like "Umori → Ravenous Slime" that
    shared nothing. A pair now requires a shared category; nothing is invented
    to fill a column.
    """
    swaps: list[dict[str, Any]] = []
    used: set[str] = set()
    for cut in cuts:
        if cut["evidence"] == "colour identity":
            continue
        card = deck.find(cut["name"])
        cut_categories = set(tagmod.card_categories(card)) if card else set()
        if cut["evidence"].startswith("oversupplied: "):
            cut_categories.add(cut["evidence"].split(": ", 1)[1])
        for entry in adds:
            if entry["name"] in used or entry["group"] == GROUP_META:
                continue
            if entry.get("category") and entry["category"] in cut_categories:
                used.add(entry["name"])
                swaps.append(
                    {
                        "cut": cut["name"],
                        "add": entry["name"],
                        "shared": entry["category"],
                    }
                )
                break
    return swaps
