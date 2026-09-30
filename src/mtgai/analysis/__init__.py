"""Deck analysis orchestration.

`analyse()` runs every check and returns one structured result. The network-
dependent parts (EDHREC, Commander Spellbook) are optional: with `offline=True`
or when a source is unreachable, the rest of the analysis still runs, which is
what lets a phone session re-analyse a deck with no connectivity budget at all.
"""

from __future__ import annotations

from typing import Any

from ..model import Deck
from . import bracket, combos, curve, edhrec_delta, engine, legality, mana, roles, suggest, trim


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


# Engine tag categories that measure the same job as an oracle-text role.
CATEGORY_TO_ROLE = {
    "ramp": "ramp",
    "draw": "draw",
    "removal": "removal",
    "sweeper": "wipe",
    "tutor": "tutor",
    "protection": "protection",
    "recursion": "recursion",
}


def _reconcile_roles(roles_result: dict[str, Any], engine_result: dict[str, Any]) -> None:
    """Two counting systems must not contradict each other in one report.

    Oracle-text regexes and curated tags measure the same jobs; where the tags
    say a role is adequately covered, a regex-derived "too few" finding is
    noise, not signal — the tags are the better instrument, so it is dropped.

    The full category profile is used, not only the clusters: two sweepers
    never form a cluster, and reading them as "no tag data" left a wrong
    "0 wipes" standing.
    """
    profile = engine_result.get("profile") or engine_result.get("clusters") or {}
    tag_counts = {role: profile.get(cat, 0) for cat, role in CATEGORY_TO_ROLE.items()}
    roles_result["tag_counts"] = tag_counts

    kept = []
    for finding in roles_result.get("findings", []):
        tag_count = tag_counts.get(finding["role"])
        if tag_count is not None:
            low, high = finding["target"]
            if finding["verdict"] == "low" and tag_count >= low:
                continue  # tags say this job is covered
            if finding["verdict"] == "high" and tag_count <= high:
                continue
            finding["message"] += f" (tags count {tag_count})"
        kept.append(finding)
    roles_result["findings"] = kept


def _tutors_find_combos(engine_result: dict[str, Any], combos_result: dict[str, Any]) -> None:
    """A deck that assembles combos keeps its tutors: they find the pieces.

    The generic "about four tutors" is a norm for decks that win on board.
    Raggadraga runs nine tutors and a dozen combos; calling that a surplus
    offered Chord of Calling and Imperial Recruiter as cuts.
    """
    complete = combos_result.get("complete") or []
    if not complete:
        return
    for entry in engine_result.get("oversupplied") or []:
        if entry["category"] == "tutor" and entry.get("cuttable"):
            entry["cuttable"] = 0
            entry["note"] = (
                f"{len(complete)} combo{'s' if len(complete) > 1 else ''} in the deck, "
                "and tutors are how it finds the pieces"
            )


def analyse(deck: Deck, *, offline: bool = False, intent=None) -> dict[str, Any]:
    """Run the full analysis. Roles are tagged first; everything else uses them.

    `intent` is the declared deck intent (`mtgai.intent.DeckIntent`) when
    intent.md exists — it outranks inference wherever the two overlap.
    """
    roles.tag_deck(deck)

    engine_result = engine.analyse(deck, intent)
    curve_result = curve.analyse(deck)
    roles_result = roles.analyse(deck)
    _reconcile_roles(roles_result, engine_result)
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
        "roles": roles_result,
        "price": price_summary(deck),
    }
    if intent is not None:
        result["intent"] = intent.to_dict()

    if offline:
        result["edhrec"] = {"available": False, "reason": "offline mode"}
        result["combos"] = {"available": False, "complete": [], "near_miss": []}
    else:
        result["edhrec"] = edhrec_delta.analyse(deck, intent=intent)
        result["combos"] = combos.analyse(deck)

    _tutors_find_combos(engine_result, result["combos"])

    target, target_source = bracket.target_for(deck, intent)
    result["bracket"] = bracket.analyse(
        deck,
        combos=result["combos"].get("complete") or [],
        target=target,
        target_source=target_source,
    )

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

    eng = result.get("engine") or {}
    fixes = eng.get("mana_fixes") or []
    for fix in fixes[:2]:
        plural = "s" if fix["delta"] > 1 else ""
        lines.append(
            f"Fix the mana first: +{fix['delta']} {fix['color_name']} source{plural} — "
            f"{', '.join(fix['driven_by'][:3])} want ~{fix['needed']}, deck has {fix['have']}."
        )

    if result["mana"].get("land_finding"):
        lines.append(result["mana"]["land_finding"])
    # A colour the fix line already covers does not need its generic twin.
    fixed_colors = {f["color_name"] for f in fixes}
    for finding in result["mana"].get("findings", []):
        if any(finding["message"].startswith(f"{c}:") for c in fixed_colors):
            continue
        lines.append(finding["message"])

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

    # A bracket-2 player keeping Game Changers in the maybeboard should hear
    # about it before they get tempted.
    maybe = result["bracket"].get("maybeboard") or {}
    if maybe.get("warnings") and (result["bracket"].get("declared") or 2) <= 2:
        lines.append(f"Maybeboard: {maybe['warnings'][0]}.")

    return lines
