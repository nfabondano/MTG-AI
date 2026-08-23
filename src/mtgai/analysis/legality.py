"""Commander legality and deck construction rules.

These are the only checks in the tool that produce hard errors rather than
advice. A colour-identity violation or a duplicate card makes the deck illegal,
and it is easy to introduce one by accident while editing on Archidekt.
"""

from __future__ import annotations

from collections import Counter
from datetime import date

from ..model import COLOR_NAMES, Deck

DECK_SIZE = 100


def analyse(deck: Deck) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    if not deck.is_commander:
        return {
            "format": deck.format_name,
            "checked": False,
            "errors": [],
            "warnings": [
                f"Deck is {deck.format_name}, not Commander — EDH rules not applied."
            ],
            "legal": True,
        }

    total = deck.total_cards
    if total != DECK_SIZE:
        errors.append(f"Deck has {total} cards; Commander requires exactly {DECK_SIZE}.")

    commanders = deck.commanders
    if not commanders:
        errors.append("No card is marked as the commander in Archidekt.")
    elif len(commanders) > 2:
        errors.append(f"{len(commanders)} commanders marked; the maximum is 2 (partners).")

    # Singleton, basics exempt.
    seen: Counter = Counter()
    for card in deck.cards:
        if card.is_basic_land:
            continue
        seen[card.name] += card.quantity
    duplicates = [f"{name} x{count}" for name, count in seen.items() if count > 1]
    if duplicates:
        errors.append("Singleton rule broken: " + ", ".join(sorted(duplicates)))

    # Colour identity — every card must fit inside the commander's identity.
    identity = set(deck.color_identity())
    offenders = []
    for card in deck.cards:
        if card.is_commander:
            continue
        outside = set(card.color_identity) - identity
        if outside:
            offenders.append(
                f"{card.name} ({'/'.join(sorted(outside))} outside the commander's identity)"
            )
    if offenders:
        errors.append("Colour identity violations: " + "; ".join(sorted(offenders)))

    # A card from a set that has not come out yet is reported as not legal in
    # every format, Vintage included. That is a release date, not a ban, and
    # calling it an error sends you hunting for a problem you do not have.
    illegal, unreleased = [], []
    today = date.today().isoformat()
    for card in deck.cards:
        if card.commander_legal:
            continue
        if card.released_at and card.released_at[:10] > today:
            unreleased.append(f"{card.name} ({card.set_code.upper()} releases {card.released_at[:10]})")
        else:
            illegal.append(card.name)

    if illegal:
        errors.append("Not legal in Commander: " + ", ".join(sorted(illegal)))
    if unreleased:
        warnings.append(
            "Not legal yet only because the set is unreleased: "
            + ", ".join(sorted(unreleased))
        )

    if commanders:
        for commander in commanders:
            if "Legendary" not in commander.type_line and "Creature" in commander.type_line:
                warnings.append(
                    f"{commander.name} is marked as commander but is not legendary."
                )

    return {
        "format": deck.format_name,
        "checked": True,
        "total_cards": total,
        "commanders": [c.name for c in commanders],
        "identity": [COLOR_NAMES[c] for c in deck.color_identity()],
        "errors": errors,
        "warnings": warnings,
        "legal": not errors,
    }
