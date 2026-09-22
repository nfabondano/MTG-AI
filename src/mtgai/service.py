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
from .intent import DeckIntent, apply_assignments
from .model import Deck
from .sources import archidekt, edhrec, scryfall, spellbook, tagger


def _load_intent(folder: deckfolder.DeckFolder) -> DeckIntent | None:
    """The declared intent, if intent.md exists. Parse warnings are ignored
    here — analysis must run whatever state the file is in; `deck intent
    --check` is where warnings surface."""
    parsed = folder.read_intent()
    return parsed[0] if parsed else None


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
    result = analysis.analyse(deck, offline=offline, intent=_load_intent(folder))
    # Roles are attached during analysis, so the deck is written afterwards to
    # capture them — that keeps deck.json self-describing for later sessions.
    folder.write_deck(deck)
    folder.write_analysis(report.render_analysis(result))
    folder.write_suggestions(report.render_suggestions(deck, result))
    folder.write_engine(report.render_engine(result))
    return result


def suggest(
    reference: str,
    *,
    budget: float | None = None,
    offline: bool = False,
    loose: bool = False,
    max_bracket: int | None = None,
) -> dict[str, Any]:
    """Suggestions both ways: rendered markdown and the structured build.

    max_bracket sets aside adds that would push the deck past that Commander
    bracket (Game Changers, the missing half of a two-card combo, mass land
    denial) into their own list instead of pairing them as swaps — the
    structured `suggestions` reflect this the same way the markdown does.
    """
    folder = deckfolder.resolve(reference)
    deck = folder.read_deck()
    intent = _load_intent(folder)
    result = analysis.analyse(deck, offline=offline, intent=intent)
    if budget is None and intent is not None and intent.budget_per_card is not None:
        budget = intent.budget_per_card
    built = analysis.suggest.build(
        deck, result, budget=budget, loose=loose, offline=offline
    )
    markdown = report.render_suggestions(
        deck, result, built, budget=budget, loose=loose, max_bracket=max_bracket
    )
    folder.write_suggestions(markdown)

    adds = report.augment_adds_with_bracket_impact(built["adds"], result)
    adds, raises = report.split_by_bracket(adds, max_bracket)
    raised_names = {r["name"] for r in raises}
    structured = {
        **built,
        "adds": adds,
        "raises": raises,
        "swaps": [s for s in built["swaps"] if s["add"] not in raised_names],
    }
    return {"markdown": markdown, "suggestions": structured}


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
        "intent": str(folder.intent_path),
    }
    summary["roles"] = analysis.roles.analyse(deck)["counts"]
    intent = _load_intent(folder)
    eng = analysis.engine.analyse(deck, intent)
    summary["archetype"] = eng["archetype"]
    summary["clusters"] = eng["clusters"]
    summary["tribe"] = eng["tribe"]
    summary["has_intent"] = intent is not None
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
        # On the Game Changer list: one of these takes a deck to bracket 3,
        # more than three to bracket 4.
        "game_changer": bool(data.get("game_changer")),
        "edhrec_rank": data.get("edhrec_rank"),
        "price_usd": (data.get("prices") or {}).get("usd"),
        "scryfall_uri": data.get("scryfall_uri"),
    }


def edhrec_commander(name: str, *, limit: int = 25, theme: str = "") -> dict[str, Any]:
    data = edhrec.commander_for([name])
    if data.found and theme:
        themed = edhrec.theme(data.slug, theme)
        if themed.found:
            themed.themes, themed.similar = data.themes, data.similar
            data = themed
        else:
            return {"found": False, "commander": name, "theme": theme, "reason": themed.error}
    if not data.found:
        return {"found": False, "commander": name, "reason": data.error}
    top = sorted(data.recommendations, key=lambda r: -r.synergy)[:limit]
    return {
        "found": True,
        "commander": name,
        "slug": data.slug,
        "num_decks": data.num_decks,
        "themes": data.themes,
        "similar": data.similar,
        "recommendations": [r.to_dict() for r in top],
    }


def combo_search(card_name: str, *, limit: int = 15) -> list[dict[str, Any]]:
    return [c.to_dict() for c in spellbook.combos_using(card_name)[:limit]]


def cache_refresh(*, force: bool = False) -> dict[str, Any]:
    return scryfall.refresh_cache(force=force)


def cache_status() -> dict[str, Any]:
    status = scryfall.cache_status()
    status["tags"] = tagger.status()
    return status


def tags_refresh(*, force: bool = False) -> dict[str, Any]:
    return tagger.refresh(force=force)


def deck_engine(reference: str) -> dict[str, Any]:
    """What the deck is built around, and what strains it."""
    folder = deckfolder.resolve(reference)
    deck = folder.read_deck()
    result = analysis.engine.analyse(deck, _load_intent(folder))
    result["user_edited"] = folder.engine_is_user_edited()
    return result


# --- deck intent ----------------------------------------------------------


def _inferred_intent(deck: Deck) -> dict[str, Any]:
    """What the tool would guess — the interview's pre-filled defaults."""
    eng = analysis.engine.analyse(deck)
    role = eng["commander_role"]
    return {
        "archetype": eng["archetype"],
        "tribe": eng["tribe"],
        "commander_role": role["roles"],
        "supplies": role["supplies"],
        "commander_wants": eng["commander_wants"],
        "clusters": dict(list(eng["clusters"].items())[:8]),
    }


def deck_intent_show(reference: str) -> dict[str, Any]:
    """The declared intent beside what the tool infers, for the interview."""
    folder = deckfolder.resolve(reference)
    deck = folder.read_deck()
    parsed = folder.read_intent()
    return {
        "slug": folder.slug,
        "path": str(folder.intent_path),
        "exists": parsed is not None,
        "intent": parsed[0].to_dict() if parsed else None,
        "warnings": parsed[1] if parsed else [],
        "inferred": _inferred_intent(deck),
        "archidekt": {
            "description": deck.description,
            "deck_tags": deck.deck_tags,
        },
    }


def deck_intent_init(reference: str, *, force: bool = False) -> dict[str, Any]:
    """Seed intent.md from inference and the owner's Archidekt metadata.

    Refuses to overwrite an existing file unless forced: once the file exists
    it is Nicolas's, same contract as notes.md.
    """
    folder = deckfolder.resolve(reference)
    deck = folder.read_deck()
    if folder.intent_path.exists() and not force:
        raise ValueError(
            f"{folder.intent_path} already exists — edit it directly, use "
            "`deck intent --set`, or pass --force to reseed"
        )
    inferred = _inferred_intent(deck)
    prose_parts = []
    if deck.description:
        prose_parts.append(deck.description.strip())
    prose_parts.append(
        "_Seeded from inference — replace any of it. The front matter above is "
        "what the tool obeys; this space is for the plan in your own words._"
    )
    seed = DeckIntent(
        archetype=inferred["archetype"],
        tribe=inferred["tribe"] or "",
        commander_role=list(inferred["commander_role"]),
        meta_notes=", ".join(deck.deck_tags),
        prose="\n\n".join(prose_parts),
        source="generated",
        updated_at=deckfolder.now(),
    )
    folder.write_intent(seed)
    return deck_intent_show(reference)


def deck_intent_set(reference: str, assignments: dict[str, str]) -> dict[str, Any]:
    """Apply key=value edits to intent.md, creating it from the seed if absent."""
    folder = deckfolder.resolve(reference)
    if not folder.intent_path.exists():
        deck_intent_init(reference)
    intent, parse_warnings = folder.read_intent()
    intent, edit_warnings = apply_assignments(intent, assignments)
    if "source" not in assignments:
        intent.source = "interview"
    intent.updated_at = deckfolder.now()
    folder.write_intent(intent)
    shown = deck_intent_show(reference)
    shown["warnings"] = parse_warnings + edit_warnings + shown.get("warnings", [])
    return shown
