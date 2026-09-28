"""Mana curve and type distribution."""

from __future__ import annotations

from collections import Counter

from ..model import Deck
from .cost import commander_discounts, effective_mana_value, self_discounting

# More spells than this at 5+ mana makes a 100-card deck top-heavy.
TOP_HEAVY = 12


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

    # The histogram shows printed costs, as Archidekt does; whether the deck is
    # top-heavy is judged on what its spells actually cost here.
    discounts = commander_discounts(deck)
    spells = [c for c in deck.cards if not c.is_land]
    self_discounters = [c for c in spells if self_discounting(c)]
    # Which corrections actually move a card out of the 5+ bracket.
    discounted = [
        c for c in spells
        if c.mana_value >= 5 and not self_discounting(c)
        and effective_mana_value(c, discounts) < 5
    ]
    heavy_self = [c for c in self_discounters if c.mana_value >= 5]
    effective = sum(
        c.quantity
        for c in spells
        if not self_discounting(c) and effective_mana_value(c, discounts) >= 5
    )

    findings = []
    if average > 3.6:
        findings.append(
            f"Average mana value {average} is high; hands can be slow to get going."
        )
    if nonland_count and cheap / nonland_count < 0.30:
        findings.append(
            f"Only {cheap} spells at 2 mana or less — early turns may be empty."
        )
    if effective == expensive:
        if expensive > TOP_HEAVY:
            findings.append(
                f"{expensive} spells at 5+ mana is top-heavy for a 100-card deck."
            )
    else:
        reasons = []
        if discounted:
            d = next(d for d in discounts if d.applies_to(discounted[0]))
            reasons.append(d.phrase)
        if heavy_self:
            reasons.append("spells that discount themselves")
        counting = " and ".join(reasons)
        if effective > TOP_HEAVY:
            findings.append(
                f"{expensive} spells at 5+ mana ({effective} counting {counting}) "
                "is top-heavy for a 100-card deck."
            )
        elif expensive > TOP_HEAVY:
            findings.append(
                f"{expensive} spells at 5+ mana, but only {effective} counting "
                f"{counting} — lighter than it looks."
            )

    return {
        "histogram": {str(mv): histogram.get(mv, 0) for mv in range(8)},
        "average_mana_value": average,
        "nonland_count": nonland_count,
        "cheap_spells": cheap,
        "expensive_spells": expensive,
        "effective_expensive": effective,
        "discounts": [
            {"source": d.source, "what": d.what, "amount": d.amount} for d in discounts
        ],
        "self_discounting": [c.name for c in self_discounters],
        "types": dict(types.most_common()),
        "findings": findings,
    }
