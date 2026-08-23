"""Deck analysis orchestration.

`analyse()` runs every check and returns one structured result. The network-
dependent parts (EDHREC, Commander Spellbook) are optional: with `offline=True`
or when a source is unreachable, the rest of the analysis still runs, which is
what lets a phone session re-analyse a deck with no connectivity budget at all.
"""

from __future__ import annotations

from typing import Any

from ..model import Deck
from . import bracket, combos, curve, edhrec_delta, engine, legality, mana, roles


def price_summary(deck: Deck, *, top: int = 10) -> dict[str, Any]:
    priced = [c for c in deck.cards if c.price_usd is not None]
    total = sum((c.price_usd or 0) * c.quantity for c in deck.cards)
    expensive = sorted(priced, key=lambda c: -(c.price_usd or 0))[:top]
    return {
        "total_usd": round(total, 2),
        "priced_cards": len(priced),
        "unpriced_cards": len(deck.cards) - len(priced),
        "most_expensive": [
            {"name": c.name, "usd": c.price_usd} for c in expensive if c.price_usd
        ],
    }


def analyse(deck: Deck, *, offline: bool = False) -> dict[str, Any]:
    """Run the full analysis. Roles are tagged first; everything else uses them."""
    roles.tag_deck(deck)

    engine_result = engine.analyse(deck)
    curve_result = curve.analyse(deck)
    result: dict[str, Any] = {
        "deck": {
            "slug": deck.slug,
            "name": deck.name,
            "url": deck.url,
            "format": deck.format_name,
            "commanders": [c.name for c in deck.commanders],
            "total_cards": deck.total_cards,
            "color_identity": deck.color_identity(),
            "updated_at": deck.updated_at,
        },
        "engine": engine_result,
        "legality": legality.analyse(deck),
        "curve": curve_result,
        "mana": mana.analyse(deck, curve_result["average_mana_value"]),
        "roles": roles.analyse(deck),
        "price": price_summary(deck),
    }

    if offline:
        result["edhrec"] = {"available": False, "reason": "offline mode"}
        result["combos"] = {"available": False, "complete": [], "near_miss": []}
    else:
        result["edhrec"] = edhrec_delta.analyse(deck)
        result["combos"] = combos.analyse(deck)

    combo_count = len(result["combos"].get("complete") or [])
    result["bracket"] = bracket.analyse(deck, combo_count=combo_count)

    result["headline"] = _headline(result)
    return result


def _headline(result: dict[str, Any]) -> list[str]:
    """The handful of things worth reading first.

    Ordered by how much they should change what you do: illegal beats unsound
    mana, which beats a missing role, which beats a statistical curiosity.
    """
    lines: list[str] = []

    for error in result["legality"].get("errors", []):
        lines.append(f"Illegal: {error}")

    if result["mana"].get("land_finding"):
        lines.append(result["mana"]["land_finding"])
    for finding in result["mana"].get("findings", []):
        lines.append(finding["message"])

    eng = result.get("engine") or {}
    for entry in (eng.get("castability") or [])[:2]:
        if entry.get("sole_driver"):
            lines.append(
                f"{entry['name']} `{entry['mana_cost']}` is the only card holding your "
                f"colour requirement that high — {entry['reasons'][0]}."
            )

    for finding in result["roles"].get("findings", []):
        lines.append(finding["message"])

    lines.extend(result["curve"].get("findings", []))

    near = result["combos"].get("near_miss") or []
    if near:
        lines.append(
            f"{len(near)} combo{'s' if len(near) > 1 else ''} one card away — see the combo section."
        )

    if result["bracket"].get("mismatch"):
        lines.append(result["bracket"]["mismatch"])

    return lines
