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

from .. import history as history_mod
from .. import tags as tagmod
from ..model import CardEntry, Deck
from ..sources import scryfall, tagger
from . import cost as cost_mod
from . import curve as curve_mod
from . import engine as engine_mod
from . import roles as roles_mod

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
        "decided": decided(deck, result, cuts),
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

    # Combos one card away are the most concrete change available.
    for combo in (result.get("combos", {}).get("near_miss") or [])[:5]:
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
    3. **Curve** — a top-of-curve card in a deck that is already top-heavy,
       judged on what it costs here rather than what is printed on it.
    4. **No engine participation** — it does nothing the deck is built around.

    A card outside the colour identity is not a candidate but a requirement,
    and a card declared sacred in intent.md is refused everywhere else.

    Evidence 2-4 (and weak signals) is judgement, and judgement yields to what
    the deck already says about a card — see `_soft_protection`.
    """
    cuts: list[dict[str, Any]] = []
    seen: set[str] = set()
    eng = result.get("engine") or {}
    intent_data = result.get("intent") or {}
    sacred = {
        name.split("//")[0].strip().lower()
        for name in intent_data.get("core_cards") or []
    }
    flexible = _flexible(result)

    def add(name: str, why: str, evidence: str, score: int) -> None:
        key = name.split("//")[0].strip().lower()
        if key in seen:
            return
        # Declared untouchable. Colour-identity violations are the one
        # exception: an illegal card is a fact, not a suggestion.
        if key in sacred and evidence != "colour identity":
            return
        seen.add(key)
        cuts.append({"name": name, "why": why, "evidence": evidence, "score": score})

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
    shielded = _soft_protection(deck, result)

    def soft_ok(card: CardEntry | None) -> bool:
        """Whether soft evidence may offer this card at all."""
        if card is None or card.is_commander or card.name in protected:
            return False
        return history_mod.key(card.name) not in shielded

    # 1. Castability.
    for entry in (eng.get("castability_cuts") or [])[:6]:
        if entry["name"] in protected:
            continue
        why = "; ".join(entry["reasons"])
        if entry.get("sole_driver"):
            why += " — and nothing else in the deck asks this much of that colour, so cutting it relaxes the whole mana base"
        if entry.get("alternative"):
            why += f" (or {entry['alternative']})"
        add(entry["name"], why, "castability", 100 + entry.get("severity", 0))

    # 2. Oversupply — name the specific cards making up the excess. The
    #    representative must not be one of the engine's own pieces: a card that
    #    also serves a core cluster, or is the tribe, is doing double duty and
    #    offering it would repeat the Ashnod's Altar mistake.
    core = set(eng.get("core") or [])
    tribe = eng.get("tribe")
    for over in (eng.get("oversupplied") or [])[:3]:
        category = over["category"]
        members = engine_mod.cards_in_category(deck, category)
        members.sort(
            key=lambda c: (
                c.name.split("//")[0].strip().lower() not in flexible,
                -(c.mana_value or 0),
                c.name,
            )
        )
        for card in members:
            if not soft_ok(card):
                continue
            if set(card.engine_participation) & core:
                continue
            if engine_mod.is_tribe_member(card, tribe):
                continue
            add(
                card.name,
                f"{over['count']} cards do {category} work; a deck wants about "
                f"{over['target_high']}",
                f"oversupplied: {category}",
                60,
            )
            break  # one representative per cluster, not a purge

    # 3. Curve — at what cards cost here. A spell that discounts itself has no
    #    fixed cost to judge, and the commander's discount is the deck's
    #    normal state: The Great Henge was once offered as "9 mana".
    curve = result.get("curve") or {}
    heavy = curve.get("effective_expensive", curve.get("expensive_spells", 0))
    if heavy > curve_mod.TOP_HEAVY:
        discounts = cost_mod.commander_discounts(deck)
        # Ties keep deck order, as the printed-cost ranking always did.
        top = sorted(
            (
                c
                for c in deck.cards
                if not c.is_land and soft_ok(c) and not cost_mod.self_discounting(c)
            ),
            key=lambda c: (
                -cost_mod.effective_mana_value(c, discounts),
                -(c.mana_value or 0),
            ),
        )
        for card in top[:2]:
            real = cost_mod.effective_mana_value(card, discounts)
            if real < CURVE_TOP:
                break
            price = f"{card.mana_value:.0f} mana"
            discount = cost_mod.discount_for(card, discounts)
            if discount and real < card.mana_value:
                price += f" ({real:.0f} with {discount.label}'s discount)"
            add(
                card.name,
                f"{price} in a deck already carrying {heavy} spells at 5+",
                "curve",
                50,
            )

    # 4. Does nothing the deck is built around.
    for name in (eng.get("orphans") or [])[:5]:
        if not soft_ok(deck.find(name)):
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
            card = deck.find(entry["name"])
            if card is not None and not soft_ok(card):
                continue
            if entry["name"] in protected:
                continue
            add(entry["name"], entry["reason"], "low inclusion (weak signal)", 10)

    cuts.sort(key=lambda c: -c["score"])
    return cuts


# The curve step offers only what really sits at the top: in a deck judged on
# its 5+ spells, a 5-drop is the middle of that bracket, not its peak.
CURVE_TOP = 6

# Roles every deck needs (roles.TARGETS), as the tag category that measures them.
_ROLE_CATEGORY = {"wipe": "sweeper"}


def _flexible(result: dict[str, Any]) -> set[str]:
    """Cards intent.md marks as fair game — offered first, never shielded."""
    intent_data = result.get("intent") or {}
    return {
        name.split("//")[0].strip().lower()
        for name in intent_data.get("flexible_cards") or []
    }


def _soft_protection(deck: Deck, result: dict[str, Any]) -> dict[str, str]:
    """Cards that soft evidence must not offer, keyed by front face, with why.

    Each is a case where the report already says the card matters, so offering
    it on a crowded cluster or a high curve would contradict the report:

    - it is how the deck wins (engine.md lists it under "How it ends games")
    - cutting it would leave a role every deck needs below its usual minimum
      (Slinza's two sweepers, against a minimum of two)
    - it is a modal spell-land, counted among the lands the analysis already
      judged — cutting it for being redundant draw cuts a land
    - Nicolas added it in the latest change, or kept it after the tool last
      suggested cutting it (`mtgai.history`)

    A card intent.md lists under `flexible_cards` is never shielded: what
    Nicolas declares outranks what the tool infers.
    """
    shielded: dict[str, str] = {}
    eng = result.get("engine") or {}
    for entry in eng.get("win_conditions") or []:
        shielded[history_mod.key(entry["name"])] = "how the deck wins"

    profile = tagmod.deck_profile(deck)
    thin = {
        _ROLE_CATEGORY.get(role, role)
        for role, (low, _) in roles_mod.TARGETS.items()
        if profile.get(_ROLE_CATEGORY.get(role, role), 0) <= low
    }
    for card in deck.cards:
        if card.is_land:
            continue
        if card.is_modal_land:
            shielded.setdefault(history_mod.key(card.name), "counts as a land")
        held = tagmod.card_categories(card) & thin
        if held:
            shielded.setdefault(
                history_mod.key(card.name),
                f"one of too few {'/'.join(sorted(held))} cards",
            )

    for name_key, why in history_mod.deliberate(deck).items():
        shielded.setdefault(name_key, why)
    for name_key in _flexible(result):
        shielded.pop(name_key, None)
    return shielded


def decided(deck: Deck, result: dict[str, Any], cuts: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Nicolas's recent decisions that suggestions are leaving alone, for display."""
    offered = {history_mod.key(c["name"]) for c in cuts}
    chosen = history_mod.deliberate(deck, _flexible(result))
    return [
        {"name": card.name, "why": chosen[history_mod.key(card.name)]}
        for card in deck.cards
        if history_mod.key(card.name) in chosen and history_mod.key(card.name) not in offered
    ]


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
