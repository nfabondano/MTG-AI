"""What the deck is actually trying to do, and which cards do it.

The tool used to model a deck as a bag of independent cards judged against
EDHREC averages. That is how it came to flag an entire aristocrats engine —
Ayara, Yawgmoth, Pitiless Plunderer, Species Specialist — as "unusual
inclusions" whose only crime was sitting outside a truncated popularity list.

This module replaces popularity with three deck-internal signals, all of which
survive on a commander nobody has built yet:

- **Engine clusters.** Functional tags reveal the archetype directly: a deck
  with 15 sacrifice outlets, 15 copy effects and 9 death triggers is a
  clone-aristocrats deck, whatever EDHREC has heard of.
- **Castability strain.** Which specific cards demand more coloured mana than
  the deck can reliably produce. This is the signal that actually explains a
  good cut, and the tool had the data all along without connecting it to a
  card.
- **Cluster oversupply.** A deck does not need seventeen ramp spells. A card in
  an overstuffed cluster is redundant in a way that has nothing to do with
  whether strangers play it.
"""

from __future__ import annotations

import re
from collections import Counter

from .. import tags as tagmod
from ..model import COLOR_NAMES, COLORS, CardEntry, Deck
from .mana import SOURCES_FOR_PIPS, _sources_by_color

# A cluster needs this many cards before it counts as part of the plan rather
# than incidental overlap.
CLUSTER_THRESHOLD = 5

# Roughly how many cards a deck wants in each category. Above the upper bound
# is redundancy worth spending on something else.
CATEGORY_TARGETS: dict[str, tuple[int, int]] = {
    "ramp": (8, 12),
    "draw": (8, 12),
    "removal": (6, 10),
    "sweeper": (2, 4),
    "sacrifice": (4, 8),
    "tutor": (0, 4),
    "protection": (2, 6),
    "recursion": (2, 6),
}

# What a commander's own tags imply the deck should value. This is the piece
# that encodes "understand the idea of the deck": Uugguu is `death-trigger` +
# `copy` + `tokens` + `typal`, so sacrifice outlets and clones are core to it,
# not filler.
COMMANDER_WANTS: dict[str, tuple[str, ...]] = {
    "death-trigger": ("sacrifice", "death-trigger", "drain", "recursion"),
    "sacrifice": ("sacrifice", "death-trigger", "drain", "tokens"),
    "tokens": ("tokens", "sacrifice", "drain"),
    "copy": ("copy", "tokens"),
    "drain": ("drain", "sacrifice", "tokens"),
    "typal": ("typal", "copy"),
    "recursion": ("recursion", "sacrifice", "death-trigger"),
    "counters": ("counters",),
    "untap": ("untap", "ramp"),
}

# When a commander arrives sparsely tagged — new sets ship before taggers catch
# up — its oracle text still says what it is about.
_ORACLE_WANTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bdies\b|\bdie\b|\bwhen(ever)? .* is put into a graveyard", re.I), "death-trigger"),
    (re.compile(r"\bsacrifice\b", re.I), "sacrifice"),
    (re.compile(r"\bcreate\b.*\btokens?\b", re.I | re.S), "tokens"),
    (re.compile(r"\bcop(y|ies)\b", re.I), "copy"),
    (re.compile(r"\bdraws? (a|two|three|x) cards?\b", re.I), "draw"),
    (re.compile(r"loses? \d+ life|loses? life|drain", re.I), "drain"),
    (re.compile(r"\+1/\+1 counter", re.I), "counters"),
)

# A tribe only counts as the deck's tribe when the deck actually commits to it.
TRIBE_MIN = 4

# A cluster the commander's own text asks for gets to run deeper before it is
# called oversupplied — for a death-trigger commander, fourteen sacrifice
# outlets are the deck working, not bloat.
CORE_TARGET_MULTIPLIER = 2


def commander_wants(deck: Deck) -> list[str]:
    """Categories the commander's own text implies the deck is built around."""
    wants: set[str] = set()
    for commander in deck.commanders:
        base = set(tagmod.card_categories(commander))
        for pattern, category in _ORACLE_WANTS:
            if pattern.search(commander.role_text()):
                base.add(category)
        for category in base:
            wants.add(category)
            wants.update(COMMANDER_WANTS.get(category, ()))
    if commander_tribe(deck):
        wants.add("typal")
        wants.update(COMMANDER_WANTS.get("typal", ()))
    return sorted(wants)


# Commander design roles, after JoeyDH's framing: is the commander the setup
# or the payoff? A force multiplier, or one of a kind? The answer says what
# the 99 must supply — a payoff commander needs the 99 to provide the fuel and
# the triggers; an enabler needs payoffs; a standalone threat needs protection.
_ROLE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"whenever (another|a|one or more|you)\b.*\b(dies|die|enters|attacks?|cast|sacrifice|gain|lose|draw)", re.I | re.S), "payoff"),
    (re.compile(r"\{t\}[^:]*:.*\b(add|create|draw|search|return)\b", re.I | re.S), "enabler"),
    (re.compile(r"sacrifice (a|another|an) [^:]*:", re.I), "enabler"),
    (re.compile(r"\bcop(y|ies)\b|\bdouble\b|\btwice\b|\badditional\b", re.I), "force-multiplier"),
    (re.compile(r"creatures? you control (get|have)\s*\+", re.I), "force-multiplier"),
    (re.compile(r"(flying|menace|trample|can't be blocked|double strike).*(deals? combat damage|whenever [^.]* attacks)", re.I | re.S), "wincon"),
    (re.compile(r"(hexproof|ward|indestructible|protection from)", re.I), "standalone"),
)


def classify_commander(deck: Deck) -> dict:
    """The commander's design role(s) and what the 99 must therefore supply."""
    roles: list[str] = []
    for commander in deck.commanders:
        text = commander.role_text()
        for pattern, role in _ROLE_PATTERNS:
            if role not in roles and pattern.search(text):
                roles.append(role)
    if not roles:
        roles = ["glue"]

    wants = set(commander_wants(deck))
    tribe = commander_tribe(deck)
    supplies: list[str] = []
    if "payoff" in roles:
        if "death-trigger" in wants or "sacrifice" in wants:
            fuel = f"nontoken {tribe}s worth copying" if tribe else "creatures the trigger counts"
            supplies.append(f"fuel: {fuel}")
            supplies.append("triggers: sacrifice outlets and asymmetric wipes, so deaths happen on your terms")
            supplies.append("conversion: drain, draw and token payoffs that turn deaths into wins")
        else:
            supplies.append("fuel: cards that cause what the commander rewards")
            supplies.append("conversion: payoffs that turn the reward into wins")
    if "enabler" in roles:
        supplies.append("payoffs: cards that want what the commander produces")
    if "force-multiplier" in roles and "payoff" not in roles:
        supplies.append("things worth multiplying")
    if "standalone" in roles or "wincon" in roles:
        supplies.append("protection: the deck leans on the commander surviving")
    if roles == ["glue"]:
        supplies.append("a plan of its own — the commander supports rather than defines it")

    return {"roles": roles, "supplies": supplies, "tribe": tribe}


def commander_tribe(deck: Deck) -> str | None:
    """The tribe the commander cares about, if the deck actually plays it.

    Tried in order of how directly the source speaks: the commander's own
    `typal-<tribe>` tags, then its type line, then creature types its text
    names — each cross-checked against what the deck really contains.
    """
    census = _subtype_census(deck)
    if not census:
        return None

    def confirmed(candidates: set[str]) -> str | None:
        present = [(census.get(c.lower(), 0), c) for c in candidates]
        present = [(count, name) for count, name in present if count >= TRIBE_MIN]
        if not present:
            return None
        return max(present)[1]

    for commander in deck.commanders:
        found = confirmed({t.capitalize() for t in tagmod.typal_subtypes(commander)})
        if found:
            return found

    for commander in deck.commanders:
        found = confirmed(commander.subtypes)
        if found:
            return found

    for commander in deck.commanders:
        mentioned = {w.strip(",.") for w in commander.role_text().split()}
        found = confirmed({w for w in mentioned if w.lower() in census})
        if found:
            return found
    return None


def _subtype_census(deck: Deck) -> dict[str, int]:
    """How many nonland cards carry each subtype, changelings counted for all."""
    census: Counter = Counter()
    for card in deck.cards:
        if card.is_land:
            continue
        for subtype in card.subtypes:
            census[subtype.lower()] += card.quantity
    return dict(census)


def tribe_census(deck: Deck, tribe: str | None) -> dict:
    """Who is in the tribe — by type line, by changeling, and conditionally.

    Clones and copy effects become the tribe when they copy a member — a blue
    clone of an Ooze *is* an Ooze — so they are counted as conditional members
    rather than outsiders.
    """
    if not tribe:
        return {}
    key = tribe.lower()
    true_type = changelings = conditional = 0
    for card in deck.cards:
        if card.is_land:
            continue
        if key in {s.lower() for s in card.subtypes}:
            true_type += card.quantity
        elif card.is_changeling:
            changelings += card.quantity
        elif "copy" in tagmod.card_categories(card):
            conditional += card.quantity
    return {
        "tribe": tribe,
        "true_type": true_type,
        "changelings": changelings,
        "conditional_copies": conditional,
    }


def is_tribe_member(card: CardEntry, tribe: str | None) -> bool:
    """True membership only — clones are conditional and judged separately."""
    if not tribe or card.is_land:
        return False
    if card.is_changeling:
        return True
    return tribe.lower() in {s.lower() for s in card.subtypes}


def clusters(deck: Deck) -> dict[str, int]:
    """Functional categories the deck invests in, with their card counts."""
    profile = tagmod.deck_profile(deck)
    return {
        category: count
        for category, count in profile.most_common()
        if count >= CLUSTER_THRESHOLD
    }


# The owner's own Archidekt category names are ground truth about what a card
# is *for* in this deck. Slugified name -> engine category.
OWNER_CATEGORY_MAP: dict[str, str] = {
    "sac-outlet": "sacrifice",
    "sac-outlets": "sacrifice",
    "sacrifice": "sacrifice",
    "sacrifice-outlets": "sacrifice",
    "clone": "copy",
    "clones": "copy",
    "copy": "copy",
    "copies": "copy",
    "drain": "drain",
    "aristocrats": "drain",
    "token": "tokens",
    "tokens": "tokens",
    "draw": "draw",
    "card-draw": "draw",
    "card-advantage": "draw",
    "ramp": "ramp",
    "mana": "ramp",
    "recursion": "recursion",
    "reanimation": "recursion",
    "removal": "removal",
    "interaction": "removal",
    "protection": "protection",
    "tutor": "tutor",
    "tutors": "tutor",
    "sweeper": "sweeper",
    "sweepers": "sweeper",
    "board-wipes": "sweeper",
    "wipes": "sweeper",
    "finisher": "wincon",
    "finishers": "wincon",
    "wincon": "wincon",
    "wincons": "wincon",
    "win-conditions": "wincon",
}


def owner_categories(card: CardEntry) -> set[str]:
    """Engine categories the owner's own Archidekt categories map onto."""
    return {
        OWNER_CATEGORY_MAP[slug]
        for slug in (tagmod.slugify(c) for c in card.categories or [])
        if slug in OWNER_CATEGORY_MAP
    }


def tag_participation(deck: Deck, tribe: str | None = None) -> None:
    """Record on each card which of the deck's clusters it belongs to.

    Membership comes from functional tags, from the owner's own Archidekt
    categories, and — in a typal deck — from simply being the tribe: an Ooze in
    the Ooze deck participates by existing, whatever its text tags say. That is
    the rule whose absence once marked two Oozes as doing nothing.
    """
    if tribe is None:
        tribe = commander_tribe(deck)
    active = set(clusters(deck)) | set(commander_wants(deck))
    for card in deck.cards:
        if card.is_land:
            card.engine_participation = []
            continue
        member = tagmod.card_categories(card) | owner_categories(card)
        if "typal" in active and is_tribe_member(card, tribe):
            member.add("typal")
        card.engine_participation = sorted(member & active)


def castability(deck: Deck) -> list[dict]:
    """Cards that ask for more coloured mana than the deck reliably produces.

    Two things make a card hard to cast: demanding many pips of one colour, and
    demanding several different colours at once. Both are measured against the
    deck's real source counts rather than a rule of thumb.

    A card is the `sole_driver` of a colour when nothing else in the deck asks
    for as many pips of it — cutting that one card relaxes the whole mana base,
    which is exactly why `{B}{B}{B}` Ayara was the right cut from a deck sitting
    one black source short.
    """
    identity = set(deck.color_identity())
    _, sources = _sources_by_color(deck)

    # The heaviest demand per colour, and how many cards make that demand.
    peak: dict[str, int] = {c: 0 for c in COLORS}
    demand_counts: dict[tuple[str, int], int] = {}
    for card in deck.cards:
        if card.is_land:
            continue
        for color, count in card.pips().items():
            if not count:
                continue
            peak[color] = max(peak[color], count)
            demand_counts[(color, count)] = demand_counts.get((color, count), 0) + 1

    findings: list[dict] = []
    for card in deck.cards:
        if card.is_land or card.is_commander:
            continue
        pips = {c: n for c, n in card.pips().items() if n}
        if not pips:
            continue

        worst_short = 0
        reasons: list[str] = []
        sole = False
        short_colors: dict[str, dict[str, int]] = {}

        for color, count in pips.items():
            needed = SOURCES_FOR_PIPS.get(min(count, 3), 0)
            have = sources.get(color, 0)
            short = needed - have
            if short > 0:
                worst_short = max(worst_short, short)
                short_colors[color] = {"needed": needed, "have": have, "short": short}
                reasons.append(
                    f"{count} {COLOR_NAMES.get(color, color)} pips wants ~{needed} "
                    f"sources, deck has {have}"
                )
                # Nothing else asks this much of the colour, so this card alone
                # is holding the requirement up.
                if count == peak[color] and demand_counts.get((color, count), 0) == 1:
                    sole = True

        distinct = len([c for c in pips if c in identity])
        if distinct >= 3:
            reasons.append(f"needs {distinct} different colours in one cost")

        if reasons:
            # Being the sole reason a colour requirement is high matters most:
            # cutting that card relaxes the whole mana base. A three-colour cost
            # outranks being one source short of a two-pip card.
            severity = worst_short * 2
            if sole:
                severity += 10
            if distinct >= 3:
                severity += 6

            findings.append(
                {
                    "name": card.name,
                    "mana_cost": card.mana_cost,
                    "shortfall": worst_short,
                    "distinct_colors": distinct,
                    "sole_driver": sole,
                    "severity": severity,
                    "reasons": reasons,
                    "short_colors": short_colors,
                }
            )

    findings.sort(key=lambda f: -f["severity"])
    return findings


def oversupplied(
    deck: Deck,
    wants: set[str] | None = None,
    overrides: dict[str, int] | None = None,
) -> list[dict]:
    """Clusters holding more cards than a deck normally needs.

    A category the commander's own text asks for is judged against a doubled
    bound: it is the deck's engine, and the generic target would flag the deck
    for doing the thing it is built to do. If even the doubled bound is passed,
    the finding says so honestly instead of pretending the engine is filler.

    A declared intent overrides both: `core_categories: {ramp: 20}` means
    Nicolas wants twenty, and twenty is the bound.
    """
    if wants is None:
        wants = set(commander_wants(deck))
    overrides = overrides or {}
    profile = tagmod.deck_profile(deck)
    out = []
    for category, (_, high) in CATEGORY_TARGETS.items():
        core = category in wants
        bound = high * CORE_TARGET_MULTIPLIER if core else high
        if category in overrides:
            bound = overrides[category]
            core = True
        count = profile.get(category, 0)
        if count > bound:
            out.append(
                {
                    "category": category,
                    "count": count,
                    "target_high": bound,
                    "excess": count - bound,
                    "commander_core": core,
                }
            )
    out.sort(key=lambda e: -e["excess"])
    return out


def cards_in_category(deck: Deck, category: str) -> list[CardEntry]:
    return [
        c
        for c in deck.cards
        if not c.is_land and category in tagmod.card_categories(c)
    ]


# Below this, a card is merely a bit awkward rather than a problem. A one- or
# two-source shortfall shared by half the blue cards is a mana-base finding —
# the answer is more sources, not cutting six spells.
CUT_SEVERITY = 6


def mana_fixes(deck: Deck, demoted: list[dict]) -> list[dict]:
    """Turn castability strain on engine cards into mana-base advice.

    A shortfall shared by the deck's own payoffs means the mana base is wrong,
    not the payoffs — the answer is more sources of that colour, and the deck
    usually contains the lands worth swapping out.
    """
    by_color: dict[str, dict] = {}
    for finding in demoted:
        for color, info in (finding.get("short_colors") or {}).items():
            entry = by_color.setdefault(
                color,
                {
                    "color": color,
                    "color_name": COLOR_NAMES.get(color, color),
                    "have": info["have"],
                    "needed": info["needed"],
                    "delta": info["short"],
                    "driven_by": [],
                },
            )
            entry["needed"] = max(entry["needed"], info["needed"])
            entry["delta"] = max(entry["delta"], info["short"])
            entry["driven_by"].append(finding["name"])

    fixes = []
    for entry in by_color.values():
        swaps = [
            land.name
            for land in sorted(
                deck.lands,
                key=lambda l: (bool(l.produces()), len(l.produces()), l.name),
            )
            if entry["color"] not in land.produces() and not land.is_basic_land
        ]
        entry["swap_candidates"] = swaps[:3]
        fixes.append(entry)
    fixes.sort(key=lambda e: -e["delta"])
    return fixes


def analyse(deck: Deck, intent=None) -> dict:
    """The deck's engine: what it is built around, and what strains it.

    A declared intent (`intent.md`) outranks inference wherever the two speak
    to the same thing: the tribe, the archetype's name, and how deep a cluster
    is allowed to run.
    """
    tagmod.ensure_tags(deck)
    tribe = (getattr(intent, "tribe", "") or None) or commander_tribe(deck)
    tag_participation(deck, tribe)

    found = clusters(deck)
    wants = commander_wants(deck)
    overrides: dict[str, int] = dict(getattr(intent, "core_categories", None) or {})
    wants = sorted(set(wants) | set(overrides))
    strain = castability(deck)
    excess = oversupplied(deck, set(wants), overrides)

    # Name the archetype from the biggest clusters the commander cares about.
    # Every deck has a dozen incidental overlaps; the archetype is the top few.
    wanted = [(c, n) for c, n in found.items() if c in wants]
    wanted.sort(key=lambda t: -t[1])
    core = [c for c, _ in wanted[:3]] or list(found)[:3]

    orphans = [
        c.name
        for c in deck.cards
        if not c.is_land and not c.is_commander and not c.engine_participation
    ]

    # A hard-to-cast card that the deck is built around is a mana-base problem,
    # not a cut. Only three kinds of strain still argue for cutting the card
    # itself: it alone holds a colour requirement up, it wants three colours in
    # one cost, or it is not part of the plan anyway.
    wanted_set = set(wants)
    core_names = {
        c.name
        for c in deck.cards
        if set(c.engine_participation) & wanted_set or is_tribe_member(c, tribe)
    }
    cuts: list[dict] = []
    demoted: list[dict] = []
    for finding in strain:
        if finding["severity"] < CUT_SEVERITY:
            continue
        if (
            finding["sole_driver"]
            or finding["distinct_colors"] >= 3
            or finding["name"] not in core_names
        ):
            if finding["sole_driver"] and finding.get("short_colors"):
                worst = max(finding["short_colors"].items(), key=lambda kv: kv[1]["short"])
                finding["alternative"] = (
                    f"add {worst[1]['short']} {COLOR_NAMES.get(worst[0], worst[0])} "
                    f"source{'s' if worst[1]['short'] > 1 else ''} instead"
                )
            cuts.append(finding)
        else:
            demoted.append(finding)

    archetype_parts = list(core)
    if tribe and "typal" in archetype_parts:
        archetype_parts[archetype_parts.index("typal")] = f"{tribe} typal"
    inferred = " + ".join(archetype_parts) if archetype_parts else "no dominant theme"
    declared = getattr(intent, "archetype", "") or ""
    if declared and declared != inferred:
        archetype = f"{declared} (inferred: {inferred})"
    else:
        archetype = declared or inferred

    return {
        "commander": [c.name for c in deck.commanders],
        "commander_wants": wants,
        "commander_role": classify_commander(deck),
        "tribe": tribe,
        "tribe_census": tribe_census(deck, tribe),
        "clusters": found,
        "core": core,
        "archetype": archetype,
        "castability": strain,
        "castability_cuts": cuts,
        "mana_fixes": mana_fixes(deck, demoted),
        "oversupplied": excess,
        "orphans": orphans,
        "tag_counts": dict(tagmod.tag_counts(deck).most_common(20)),
        "note": (
            "Clusters come from human-curated functional tags, not oracle-text "
            "guessing. A card outside every cluster is not automatically bad — it "
            "may be doing something the tags do not name."
        ),
    }
