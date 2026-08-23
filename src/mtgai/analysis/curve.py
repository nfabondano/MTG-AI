"""Mana curve and type distribution."""

from __future__ import annotations

from collections import Counter

from ..model import Deck


def analyse(deck: Deck) -> dict:
    """Mana value histogram over nonland cards, plus the type spread.

    Lands are excluded from the curve: they have mana value 0 and would pile a
    third of the deck into the first bucket, hiding the real shape.
    """
    histogram: Counter = Counter()
    total_mv = 0.0
    nonland_count = 0

    for card in deck.cards:
        if card.is_land:
            continue
        bucket = min(int(card.mana_value), 7)  # 7 means "7 or more"
        histogram[bucket] += card.quantity
        total_mv += card.mana_value * card.quantity
        nonland_count += card.quantity

    average = round(total_mv / nonland_count, 2) if nonland_count else 0.0

    types: Counter = Counter()
    for card in deck.cards:
        types[card.primary_type] += card.quantity

    cheap = sum(histogram.get(i, 0) for i in (0, 1, 2))
    expensive = sum(count for mv, count in histogram.items() if mv >= 5)

    findings = []
    if average > 3.6:
        findings.append(
            f"Average mana value {average} is high; hands can be slow to get going."
        )
    if nonland_count and cheap / nonland_count < 0.30:
        findings.append(
            f"Only {cheap} spells at 2 mana or less — early turns may be empty."
        )
    if expensive > 12:
        findings.append(
            f"{expensive} spells at 5+ mana is top-heavy for a 100-card deck."
        )

    return {
        "histogram": {str(mv): histogram.get(mv, 0) for mv in range(8)},
        "average_mana_value": average,
        "nonland_count": nonland_count,
        "cheap_spells": cheap,
        "expensive_spells": expensive,
        "types": dict(types.most_common()),
        "findings": findings,
    }
