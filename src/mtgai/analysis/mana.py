"""Mana base analysis — pip demand against actual sources.

This is the highest-signal check the tool runs. The classic failure in a
homebrew EDH deck is not a bad card choice, it is 22 black pips supported by 13
black sources, so the deck stalls holding spells it cannot cast.

One subtlety drives correctness here: cards that tap for "any colour" — Command
Tower, Arcane Signet, Cavern of Souls — report all five colours in both
Archidekt's and Scryfall's data. In a Mardu deck they produce W/B/R only, so
every produced-colour set is intersected with the deck's colour identity before
being counted. Without that, a three-colour deck reports phantom sources in the
two colours it does not play.
"""

from __future__ import annotations

from collections import Counter

from ..model import COLOR_NAMES, COLORS, Deck

# Rule of thumb from EDH mana-base work (Frank Karsten's tables, rounded for
# the singleton format): sources needed to reliably cast a spell by the turn
# its mana value allows, given how many coloured pips it demands.
SOURCES_FOR_PIPS = {1: 14, 2: 20, 3: 23}


def _sources_by_color(deck: Deck) -> tuple[Counter, Counter]:
    """Coloured sources, restricted to the deck's real colour identity.

    Returns (land_sources, all_sources).
    """
    identity = set(deck.color_identity())
    lands: Counter = Counter()
    total: Counter = Counter()
    for card in deck.cards:
        produced = card.produces()
        if not produced:
            continue
        # "Any colour" producers only make what the identity allows.
        effective = produced & identity if identity else produced
        for color in effective:
            total[color] += card.quantity
            if card.is_land:
                lands[color] += card.quantity
    return lands, total


def _pips_by_color(deck: Deck) -> tuple[Counter, dict[str, int]]:
    """Total coloured pips, and the heaviest single-card demand per colour."""
    pips: Counter = Counter()
    heaviest: dict[str, int] = {c: 0 for c in COLORS}
    for card in deck.cards:
        if card.is_land:
            continue
        for color, count in card.pips().items():
            if not count:
                continue
            pips[color] += count * card.quantity
            heaviest[color] = max(heaviest[color], count)
    return pips, heaviest


def recommended_land_count(deck: Deck, avg_mv: float) -> int:
    """A land target from the curve, eased down for heavy ramp and cheap decks.

    Baseline is 37 for an average deck; a low curve or a lot of ramp genuinely
    supports fewer lands, and the reverse for a top-heavy list.
    """
    base = 37.0
    base += (avg_mv - 3.2) * 2.5

    ramp = sum(c.quantity for c in deck.cards if "ramp" in (c.roles or []))
    base -= max(0, ramp - 8) * 0.4

    return int(round(max(30.0, min(42.0, base))))


def analyse(deck: Deck, avg_mv: float) -> dict:
    """Compare pip demand to sources and judge the land count."""
    identity = deck.color_identity()
    land_sources, total_sources = _sources_by_color(deck)
    pips, heaviest = _pips_by_color(deck)

    colors = []
    findings = []
    for color in identity:
        demand = pips.get(color, 0)
        peak = heaviest.get(color, 0)
        sources = total_sources.get(color, 0)
        needed = SOURCES_FOR_PIPS.get(min(peak, 3), 0) if peak else 0

        entry = {
            "color": color,
            "name": COLOR_NAMES[color],
            "pips": demand,
            "heaviest_requirement": peak,
            "land_sources": land_sources.get(color, 0),
            "total_sources": sources,
            "recommended_sources": needed,
        }
        colors.append(entry)

        if needed and sources < needed:
            findings.append(
                {
                    "color": color,
                    "verdict": "short",
                    "message": (
                        f"{COLOR_NAMES[color]}: {sources} sources for cards needing "
                        f"{peak} {color} pip{'s' if peak > 1 else ''} — around {needed} "
                        f"is the usual target."
                    ),
                }
            )

    # A colour carrying real pip demand but almost no sources is a splash that
    # is not actually supported; worth flagging separately from a small shortfall.
    for entry in colors:
        if entry["pips"] >= 8 and entry["total_sources"] < 8:
            findings.append(
                {
                    "color": entry["color"],
                    "verdict": "unsupported",
                    "message": (
                        f"{entry['name']}: {entry['pips']} pips of demand but only "
                        f"{entry['total_sources']} sources — either commit to the colour "
                        f"or cut it."
                    ),
                }
            )

    land_count = sum(c.quantity for c in deck.lands)

    # Modal double-faced spells with a land back can be played as a land when a
    # hand needs one. They are not lands, but a deck running several of them
    # genuinely functions on a lower land count, so judging the land count
    # without them badly understates the mana base.
    modal_lands = [c for c in deck.cards if c.is_modal_land]
    modal_count = sum(c.quantity for c in modal_lands)
    effective_lands = land_count + modal_count

    recommended = recommended_land_count(deck, avg_mv)
    land_finding = None
    if effective_lands < recommended - 2:
        land_finding = (
            f"{land_count} lands against a curve suggesting about {recommended}. "
            "Expect stumbling draws."
        )
        if modal_count:
            land_finding = (
                f"{land_count} lands plus {modal_count} modal spell-lands "
                f"({effective_lands} effective) against a curve suggesting about "
                f"{recommended}. Still on the low side."
            )
    elif land_count > recommended + 3:
        land_finding = (
            f"{land_count} lands against a curve suggesting about {recommended}. "
            "You can likely cut one or two for action."
        )

    return {
        "identity": identity,
        "colors": colors,
        "land_count": land_count,
        "modal_lands": modal_count,
        "effective_lands": effective_lands,
        "recommended_lands": recommended,
        "land_finding": land_finding,
        "findings": findings,
        "note": (
            "Sources count lands plus anything that taps for mana, restricted to "
            "the deck's colour identity."
        ),
    }
