"""The operations the CLI and the MCP server both call.

Keeping the real work here means the two front ends cannot drift apart, and
that the CLI remains fully capable on its own. That matters more than it
sounds: a project `.mcp.json` is not auto-approved in a freshly cloned
repository, so on a phone the CLI is often the only path that works.
"""

from __future__ import annotations

from typing import Any

from . import analysis, deckfolder
from .analysis import report
from .model import Deck
from .sources import archidekt, edhrec, scryfall, spellbook


def add_deck(reference: str, *, offline: bool = False, refresh: bool = False) -> dict[str, Any]:
    """Import an Archidekt deck, then analyse it."""
    deck_id = archidekt.parse_deck_id(reference)
    payload = archidekt.fetch_raw(deck_id, use_cache=not refresh)

    slug = deckfolder.slugify(payload.get("name") or "", deck_id)
    deck = archidekt.normalise(payload, slug, enrich=not offline)

    folder = deckfolder.folder_for(slug)
    folder.write_source(payload)
    result = _analyse_and_write(deck, folder, offline=offline)

    return {
        "slug": slug,
        "path": str(folder.path),
        "deck": deckfolder.summarise(deck),
        "analysis": result,
    }


def refresh_deck(reference: str, *, offline: bool = False) -> dict[str, Any]:
    """Re-pull a tracked deck from Archidekt and re-analyse it."""
    folder = deckfolder.resolve(reference)
    existing = folder.read_deck()
    return add_deck(str(existing.archidekt_id), offline=offline, refresh=True)


def analyse_deck(reference: str, *, offline: bool = False) -> dict[str, Any]:
    """Re-run the analysis over the stored deck, without touching Archidekt."""
    folder = deckfolder.resolve(reference)
    deck = folder.read_deck()
    return _analyse_and_write(deck, folder, offline=offline)


def _analyse_and_write(
    deck: Deck, folder: deckfolder.DeckFolder, *, offline: bool
) -> dict[str, Any]:
    result = analysis.analyse(deck, offline=offline)
    # Roles are attached during analysis, so the deck is written afterwards to
    # capture them — that keeps deck.json self-describing for later sessions.
    folder.write_deck(deck)
    folder.write_analysis(report.render_analysis(result))
    folder.write_suggestions(report.render_suggestions(deck, result))
    return result


def suggest(reference: str, *, budget: float | None = None, offline: bool = False) -> str:
    folder = deckfolder.resolve(reference)
    deck = folder.read_deck()
    result = analysis.analyse(deck, offline=offline)
    markdown = report.render_suggestions(deck, result, budget=budget)
    folder.write_suggestions(markdown)
    return markdown


def list_decks() -> list[dict[str, Any]]:
    summaries = []
    for folder in deckfolder.all_folders():
        try:
            summaries.append(deckfolder.summarise(folder.read_deck()))
        except (OSError, ValueError, KeyError):
            continue
    return summaries


def show_deck(reference: str) -> dict[str, Any]:
    folder = deckfolder.resolve(reference)
    deck = folder.read_deck()
    summary = deckfolder.summarise(deck)
    summary["files"] = {
        "analysis": str(folder.analysis_path),
        "suggestions": str(folder.suggestions_path),
        "deck": str(folder.deck_path),
        "notes": str(folder.notes_path),
    }
    summary["roles"] = analysis.roles.analyse(deck)["counts"]
    return summary


def deck_cards(reference: str, *, role: str = "", card_type: str = "") -> list[dict[str, Any]]:
    """Filtered card list — how a session asks 'what ramp am I running?'."""
    folder = deckfolder.resolve(reference)
    deck = folder.read_deck()
    analysis.roles.tag_deck(deck)

    cards = deck.cards
    if role:
        cards = [c for c in cards if role.lower() in (c.roles or [])]
    if card_type:
        cards = [c for c in cards if card_type.lower() in c.type_line.lower()]

    return [
        {
            "name": c.name,
            "quantity": c.quantity,
            "mana_cost": c.mana_cost,
            "mana_value": c.mana_value,
            "type_line": c.type_line,
            "roles": c.roles,
            "price_usd": c.price_usd,
        }
        for c in sorted(cards, key=lambda c: (c.mana_value, c.name))
    ]


def card_lookup(name: str) -> dict[str, Any]:
    data = scryfall.lookup(name=name)
    if not data:
        return {"found": False, "name": name}
    return {
        "found": True,
        "name": data.get("name"),
        "mana_cost": data.get("mana_cost"),
        "mana_value": data.get("cmc"),
        "type_line": data.get("type_line"),
        "oracle_text": data.get("oracle_text"),
        "color_identity": data.get("color_identity"),
        "legalities": {"commander": (data.get("legalities") or {}).get("commander")},
        "edhrec_rank": data.get("edhrec_rank"),
        "price_usd": (data.get("prices") or {}).get("usd"),
        "scryfall_uri": data.get("scryfall_uri"),
    }


def edhrec_commander(name: str, *, limit: int = 25) -> dict[str, Any]:
    data = edhrec.commander_for([name])
    if not data.found:
        return {"found": False, "commander": name, "reason": data.error}
    top = sorted(data.recommendations, key=lambda r: -r.synergy)[:limit]
    return {
        "found": True,
        "commander": name,
        "slug": data.slug,
        "recommendations": [r.to_dict() for r in top],
    }


def combo_search(card_name: str, *, limit: int = 15) -> list[dict[str, Any]]:
    return [c.to_dict() for c in spellbook.combos_using(card_name)[:limit]]


def cache_refresh(*, force: bool = False) -> dict[str, Any]:
    return scryfall.refresh_cache(force=force)


def cache_status() -> dict[str, Any]:
    return scryfall.cache_status()
