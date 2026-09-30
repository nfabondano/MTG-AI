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
from .http import NotFound, SourceError
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
    """Import an Archidekt deck, then analyse it.

    A deck already tracked under an older name is the same deck: its folder
    is carried over into the new one rather than left behind as a duplicate.
    """
    deck_id = archidekt.parse_deck_id(reference)
    payload = archidekt.fetch_raw(deck_id, use_cache=not refresh)
    return _import_payload(payload, deck_id, offline=offline, previous=deckfolder.find_by_id(deck_id))


def _import_payload(
    payload: dict[str, Any],
    deck_id: int,
    *,
    offline: bool,
    previous: deckfolder.DeckFolder | None = None,
) -> dict[str, Any]:
    """Write, carry over, analyse, then retire the old folder — in that order.

    The carry-over runs before the analysis so that intent.md already speaks
    for the deck in its new folder. The old folder goes only after
    everything else succeeded, and never when a file clashed.
    """
    slug = deckfolder.slugify(payload.get("name") or "", deck_id)
    deck = archidekt.normalise(payload, slug, enrich=not offline)

    folder = deckfolder.folder_for(slug)
    folder.write_source(payload)
    moved: dict[str, Any] | None = None
    if previous is not None and previous.slug != slug and previous.path.exists():
        moved = {"from": previous.slug, **deckfolder.carry_over(previous, folder)}
    result = _analyse_and_write(deck, folder, offline=offline)
    if moved is not None:
        moved["removed"] = not moved["conflicts"]
        if moved["removed"]:
            deckfolder.remove(previous)

    out: dict[str, Any] = {
        "slug": slug,
        "path": str(folder.path),
        "deck": deckfolder.summarise(deck),
        "analysis": result,
    }
    if moved is not None:
        out["moved"] = moved
    return out


class DeckMoved(NotFound):
    """A tracked deck's Archidekt link is gone (404).

    Carries what a caller needs to fix it without guessing: the folder, the
    dead id, and the owner's decks that could be its new home, best first.
    Nothing has been changed when this is raised.
    """

    def __init__(self, folder: deckfolder.DeckFolder, deck: Deck, candidates: list[dict[str, Any]]):
        self.slug = folder.slug
        self.old_id = deck.archidekt_id
        self.name = deck.name
        self.owner = deck.owner
        self.candidates = candidates
        self.best = _best_candidate(candidates, deck.name)
        commands = []
        if self.best is not None:
            commands.append(f"mtg deck refresh {self.slug} --follow")
        commands.append(f"mtg deck relink {self.slug} <new id or url>")
        self.commands = commands

        lines = [f"Archidekt deck {self.old_id} ({self.name}) is gone — it answers 404."]
        if candidates:
            shown = "; ".join(
                f"{c['id']} “{c['name']}” (updated {c['updated_at'][:10]})" for c in candidates[:5]
            )
            lines.append(f"{self.owner}'s decks that could be it: {shown}.")
        elif self.owner:
            lines.append(
                f"{self.owner} has no public deck by that name; "
                f"`mtg deck find {self.owner}` lists the rest."
            )
        lines.append("Fix it with: " + " or ".join(f"`{c}`" for c in commands) + ".")
        super().__init__(" ".join(lines), status=404, url=f"{archidekt.API}/{self.old_id}/")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "moved",
            "slug": self.slug,
            "old_id": self.old_id,
            "name": self.name,
            "owner": self.owner,
            "candidates": self.candidates,
            "best": self.best,
            "next": self.commands,
            "message": str(self),
        }


def _best_candidate(candidates: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    """The one candidate safe to follow without asking: the only deck with
    exactly the old name, or the only deck the search found at all."""
    exact = [c for c in candidates if c["name"].strip().lower() == name.strip().lower()]
    if len(exact) == 1:
        return exact[0]
    if not exact and len(candidates) == 1:
        return candidates[0]
    return None


def _moved_candidates(deck: Deck) -> list[dict[str, Any]]:
    """The owner's decks that could be this one's new home: by the deck's
    name, then by its commander's name. The dead id itself is never one."""
    if not deck.owner:
        return []
    queries = [deck.name]
    if deck.commanders:
        queries.append(deck.commanders[0].name.split("//")[0].split(",")[0].strip())
    try:
        for query in queries:
            found = [
                c for c in archidekt.search_owner_decks(deck.owner, query)
                if c["id"] != deck.archidekt_id
            ]
            if found:
                return found
    except SourceError:
        return []
    return []


def refresh_deck(reference: str, *, offline: bool = False, follow: bool = False) -> dict[str, Any]:
    """Re-pull a tracked deck from Archidekt and re-analyse it.

    A renamed deck moves to its new folder with its notes and intent. A deck
    whose link is gone raises DeckMoved with the owner's likely new decks —
    or, with `follow`, relinks to the one candidate that is safe to follow.
    """
    folder = deckfolder.resolve(reference)
    existing = folder.read_deck()
    try:
        payload = archidekt.fetch_raw(existing.archidekt_id, use_cache=False)
    except NotFound:
        candidates = _moved_candidates(existing)
        best = _best_candidate(candidates, existing.name)
        if not (follow and best is not None):
            raise DeckMoved(folder, existing, candidates) from None
        payload = archidekt.fetch_raw(best["id"], use_cache=False)
        result = _import_payload(payload, best["id"], offline=offline, previous=folder)
        result["relinked"] = {"from": existing.archidekt_id, "to": best["id"], "followed": True}
        return result
    return _import_payload(payload, existing.archidekt_id, offline=offline, previous=folder)


def relink_deck(reference: str, new_reference: str, *, offline: bool = False) -> dict[str, Any]:
    """Point a tracked deck at a new Archidekt link, keeping what is Nicolas's.

    For a deck rebuilt under a new id: notes.md, intent.md and a hand-edited
    engine.md move to the new folder, and the old folder is removed unless
    something clashed.
    """
    folder = deckfolder.resolve(reference)
    existing = folder.read_deck()
    new_id = archidekt.parse_deck_id(new_reference)
    payload = archidekt.fetch_raw(new_id, use_cache=False)
    result = _import_payload(payload, new_id, offline=offline, previous=folder)
    result["relinked"] = {"from": existing.archidekt_id, "to": new_id, "followed": False}
    return result


def find_decks(owner: str, name: str = "") -> list[dict[str, Any]]:
    """An Archidekt user's public decks, marking the ones already tracked here."""
    decks = archidekt.search_owner_decks(owner, name)
    for entry in decks:
        tracked = deckfolder.find_by_id(entry["id"])
        entry["tracked_as"] = tracked.slug if tracked else None
    return decks


def analyse_deck(reference: str, *, offline: bool = False) -> dict[str, Any]:
    """Re-run the analysis over the stored deck, without touching Archidekt."""
    folder = deckfolder.resolve(reference)
    deck = folder.read_deck()
    return _analyse_and_write(deck, folder, offline=offline)


def _analyse_and_write(
    deck: Deck, folder: deckfolder.DeckFolder, *, offline: bool
) -> dict[str, Any]:
    intent = _load_intent(folder)
    result = analysis.analyse(deck, offline=offline, intent=intent)
    # Roles are attached during analysis, so the deck is written afterwards to
    # capture them — that keeps deck.json self-describing for later sessions.
    folder.write_deck(deck)
    folder.write_analysis(report.render_analysis(result))
    # The same defaults `suggest` applies: re-analysing must not drop the
    # declared bracket cap or budget, and offline must stay offline.
    max_bracket, _ = analysis.bracket.target_for(deck, intent)
    budget = intent.budget_per_card if intent is not None else None
    folder.write_suggestions(
        report.render_suggestions(
            deck, result, budget=budget, max_bracket=max_bracket, offline=offline,
            trim=_trim_if_oversized(deck, result),
        )
    )
    folder.write_engine(report.render_engine(result))
    return result


def _trim_if_oversized(deck: Deck, result: dict[str, Any]) -> dict[str, Any] | None:
    """A deck over 100 gets its "which ones go" answer in suggestions.md."""
    if deck.total_cards <= analysis.trim.DECK_SIZE:
        return None
    return analysis.trim.plan_trim(deck, result)


def trim_deck(
    reference: str,
    *,
    target: int = 100,
    extra: int = 3,
    max_bracket: int | None = None,
    offline: bool = False,
) -> dict[str, Any]:
    """Which cards to cut to reach `target`, plus `extra` spares to choose from.

    Every pick carries its reason and evidence; the plan also says what the
    deck looks like afterwards (size, bracket, category counts). Nothing is
    written: the list is an answer, not a file.
    """
    folder = deckfolder.resolve(reference)
    deck = folder.read_deck()
    intent = _load_intent(folder)
    result = analysis.analyse(deck, offline=offline, intent=intent)
    plan = analysis.trim.plan_trim(
        deck, result, target=target, extra=extra, max_bracket=max_bracket
    )
    plan["slug"] = folder.slug
    plan["deck"] = deck.name
    plan["markdown"] = report.render_trim(plan, heading="#")
    return plan


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
    if max_bracket is None:
        max_bracket, _ = analysis.bracket.target_for(deck, intent)
    built = analysis.suggest.build(
        deck, result, budget=budget, loose=loose, offline=offline
    )
    markdown = report.render_suggestions(
        deck, result, built, budget=budget, loose=loose, max_bracket=max_bracket,
        offline=offline, trim=_trim_if_oversized(deck, result),
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
