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


def commander_wants(deck: Deck) -> list[str]:
    """Categories the commander's own text implies the deck is built around."""
    wants: set[str] = set()
    for commander in deck.commanders:
        for category in tagmod.card_categories(commander):
            wants.add(category)
            wants.update(COMMANDER_WANTS.get(category, ()))
    return sorted(wants)


def clusters(deck: Deck) -> dict[str, int]:
    """Functional categories the deck invests in, with their card counts."""
    profile = tagmod.deck_profile(deck)
    return {
        category: count
        for category, count in profile.most_common()
        if count >= CLUSTER_THRESHOLD
    }


def tag_participation(deck: Deck) -> None:
    """Record on each card which of the deck's clusters it belongs to."""
    active = set(clusters(deck)) | set(commander_wants(deck))
    for card in deck.cards:
        if card.is_land:
            card.engine_participation = []
            continue
        card.engine_participation = sorted(tagmod.card_categories(card) & active)


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

        for color, count in pips.items():
            needed = SOURCES_FOR_PIPS.get(min(count, 3), 0)
            have = sources.get(color, 0)
            short = needed - have
            if short > 0:
                worst_short = max(worst_short, short)
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
                }
            )

    findings.sort(key=lambda f: -f["severity"])
    return findings


def oversupplied(deck: Deck) -> list[dict]:
    """Clusters holding more cards than a deck normally needs."""
    profile = tagmod.deck_profile(deck)
    out = []
    for category, (_, high) in CATEGORY_TARGETS.items():
        count = profile.get(category, 0)
        if count > high:
            out.append(
                {
                    "category": category,
                    "count": count,
                    "target_high": high,
                    "excess": count - high,
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


def analyse(deck: Deck) -> dict:
    """The deck's engine: what it is built around, and what strains it."""
    tagmod.ensure_tags(deck)
    tag_participation(deck)

    found = clusters(deck)
    wants = commander_wants(deck)
    strain = castability(deck)
    excess = oversupplied(deck)

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

    return {
        "commander": [c.name for c in deck.commanders],
        "commander_wants": wants,
        "clusters": found,
        "core": core,
        "archetype": " + ".join(core) if core else "no dominant theme",
        "castability": strain,
        "castability_cuts": [f for f in strain if f["severity"] >= CUT_SEVERITY],
        "oversupplied": excess,
        "orphans": orphans,
        "tag_counts": dict(tagmod.tag_counts(deck).most_common(20)),
        "note": (
            "Clusters come from human-curated functional tags, not oracle-text "
            "guessing. A card outside every cluster is not automatically bad — it "
            "may be doing something the tags do not name."
        ),
    }
