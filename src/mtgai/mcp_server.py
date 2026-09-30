"""MCP server exposing the deck workbench as tools.

Every tool is a thin wrapper over `service`, which is what the CLI calls too,
so the two front ends cannot drift apart.

This server is a convenience, not a dependency. A project `.mcp.json` is not
auto-approved in a freshly cloned repository, so in a cloud or phone session
these tools may sit unapproved — the slash commands run the CLI directly for
exactly that reason, and nothing is lost when they do.

Tools return the slim summaries, never raw deck payloads: an Archidekt deck is
~300 KB and would swamp a session's context.
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from . import service

INSTRUCTIONS = """\
Analyse Magic: The Gathering Commander decks imported from Archidekt. Deck
folders live in decks/<slug>/ and are committed, so they persist across
sessions and devices. Nothing is ever written back to Archidekt: Nicolas makes
the changes there by hand.

Workflow: deck_add(url) -> deck_show / deck_analyze -> deck_engine and
deck_intent BEFORE judging any card -> deck_suggest for swaps, deck_trim for
"get it to 100" (numbered cuts plus spares, each with its reason) ->
Nicolas edits on Archidekt -> deck_refresh. A deck whose link died comes back
as status "moved" with the owner's candidate decks: deck_refresh(follow=True)
or deck_relink fixes it, carrying notes.md and intent.md across.

Judgement rules the tools already follow, and you must too:
- intent.md outranks inference; its sacred cards are never cuts.
- Never recommend a cut on popularity alone; absence from an EDHREC list is
  not evidence. Popularity only breaks ties and is labelled a weak signal.
- A pick labelled "judgement call" is exactly that: say so, do not dress it
  up as a finding.
- Copy effects take the copied card's characteristics.
- Every suggested card must be inside the deck's colour identity.
- Brackets follow the October 2025 Commander rules: tutors no longer set the
  bracket; Game Changers, two-card combos, mass land denial and chained
  extra turns do.
If these tools are unavailable, `uv run mtg ...` does the same work.
"""

mcp = MCPServer("mtg-ai", instructions=INSTRUCTIONS)


def _imported(result: dict[str, Any]) -> dict[str, Any]:
    """The slim answer for anything that (re)imports a deck."""
    out = {
        "status": "ok",
        "slug": result["slug"],
        "path": result["path"],
        "deck": result["deck"],
        "headline": result["analysis"]["headline"],
    }
    for key in ("moved", "relinked"):
        if key in result:
            out[key] = result[key]
    return out


@mcp.tool()
def deck_add(reference: str, offline: bool = False) -> dict[str, Any]:
    """Import an Archidekt deck (URL or id), analyse it, and file it in decks/.

    Returns a summary plus the headline findings; the full report is written
    to the deck's folder as analysis.md. Re-adding a deck that was renamed on
    Archidekt moves its folder, notes and intent to the new name.
    """
    return _imported(service.add_deck(reference, offline=offline))


@mcp.tool()
def deck_refresh(reference: str, follow: bool = False, offline: bool = False) -> dict[str, Any]:
    """Re-pull a tracked deck from Archidekt after Nicolas edited it.

    If the deck's link is gone (404), returns status "moved" with the owner's
    decks that could be its new home, best first — nothing is changed. With
    follow=True it relinks by itself when exactly one candidate is safe (the
    only deck with the old name), carrying notes.md and intent.md across.
    """
    try:
        return _imported(service.refresh_deck(reference, offline=offline, follow=follow))
    except service.DeckMoved as moved:
        return moved.to_dict()


@mcp.tool()
def deck_relink(reference: str, new_deck: str, offline: bool = False) -> dict[str, Any]:
    """Point a tracked deck at its new Archidekt link (URL or id).

    For a deck rebuilt under a new id: notes.md, intent.md and a hand-edited
    engine.md move to the new folder; the old folder is removed unless the
    two folders hold different copies of a file, which are then both kept.
    """
    return _imported(service.relink_deck(reference, new_deck, offline=offline))


@mcp.tool()
def deck_find(owner: str, name: str = "") -> list[dict[str, Any]]:
    """An Archidekt user's public decks, optionally narrowed by name.

    Exact name first, then most recently updated; `tracked_as` names the
    local folder when the deck is already imported.
    """
    return service.find_decks(owner, name)


@mcp.tool()
def deck_list() -> list[dict[str, Any]]:
    """List every deck tracked in this repository."""
    return service.list_decks()


@mcp.tool()
def deck_show(reference: str) -> dict[str, Any]:
    """Summarise one deck: commander, colours, card counts and role counts.

    Accepts a slug, an Archidekt id, or a fragment of the deck name.
    """
    return service.show_deck(reference)


@mcp.tool()
def deck_analyze(reference: str, offline: bool = False, full: bool = False) -> dict[str, Any]:
    """Re-run the analysis on a stored deck and rewrite its reports.

    Returns the slim summary by default — headline, engine, over- and
    under-supplied jobs, bracket with its reasons, combos really present.
    full=True returns the entire result (large). offline=True skips EDHREC,
    combos and prices.
    """
    result = service.analyse_deck(reference, offline=offline)
    return result if full else service.analysis_summary(result)


@mcp.tool()
def deck_suggest(
    reference: str,
    budget: float | None = None,
    loose: bool = False,
    max_bracket: int | None = None,
    offline: bool = False,
) -> dict[str, Any]:
    """Generate cut and add suggestions for a deck, structured and slim.

    Adds are grouped: strengthens-plan and fixes-weakness carry deck-internal
    reasons; meta-optional is popularity only and appears when loose=True.
    Cuts carry deck-internal evidence (castability, oversupply, curve, no
    engine participation), mana_fixes say when the answer is sources rather
    than cuts, and swaps pair cards doing the same job. max_bracket defaults
    to the deck's target (intent.md, then Archidekt); adds that would push the
    deck past it go to `raises`. A deck over 100 also gets its `trim` plan.
    The full markdown is written to the deck's suggestions.md.
    """
    result = service.suggest(
        reference, budget=budget, loose=loose, max_bracket=max_bracket, offline=offline
    )
    built = result["suggestions"]
    slim_adds = [
        {k: a.get(k) for k in ("name", "group", "why", "price", "bracket_impact")}
        for a in built["adds"]
        if loose or a["group"] != "meta-optional"
    ][:12]
    out = {
        "max_bracket": result.get("max_bracket"),
        "mana_fixes": built["mana_fixes"],
        "adds": slim_adds,
        "cuts": [
            {k: c.get(k) for k in ("name", "why", "evidence", "alternatives", "weak_signal")}
            for c in built["cuts"][:8]
        ],
        "swaps": built["swaps"][:8],
        "hidden_meta": built["hidden_meta"],
        "raises": [
            {k: r.get(k) for k in ("name", "bracket_impact", "bracket_reason")}
            for r in built.get("raises", [])
        ][:8],
    }
    if result.get("trim"):
        out["trim"] = _slim_trim(result["trim"])
    return out


def _slim_trim(plan: dict[str, Any]) -> dict[str, Any]:
    keep = ("name", "tier", "reason", "evidence", "weak_signal", "mandatory")
    after = plan.get("after") or {}
    return {
        "total": plan["total"],
        "target": plan["target"],
        "need": plan["need"],
        "max_bracket": plan.get("max_bracket"),
        "bracket_source": plan.get("bracket_source"),
        "cuts": [{k: p.get(k) for k in keep} for p in plan.get("cuts") or []],
        "extras": [{k: p.get(k) for k in keep} for p in plan.get("extras") or []],
        "required_for_bracket": plan.get("required_for_bracket") or [],
        "protected": plan.get("protected") or {},
        "after": {k: after.get(k) for k in ("total", "bracket", "bracket_name")},
        "notes": plan.get("notes") or [],
        "land_note": (plan.get("lands") or {}).get("note"),
    }


@mcp.tool()
def deck_trim(
    reference: str,
    target: int = 100,
    extra: int = 3,
    max_bracket: int | None = None,
    offline: bool = False,
) -> dict[str, Any]:
    """Which cards to cut to reach `target` cards, plus `extra` spares.

    The answer to "it has to be 100 — which ones go, and a few in reserve".
    Picks come strongest reason first: illegal, required by the bracket,
    declared flexible, castability, oversupply, curve, no job, then
    judgement calls (labelled as such). Commander, lands, sacred cards, combo
    pieces and cards named in the declared win conditions are never offered.
    `after` is the deck as it would stand: size and recalculated bracket.
    Nothing is written.
    """
    plan = service.trim_deck(
        reference, target=target, extra=extra, max_bracket=max_bracket, offline=offline
    )
    return {"slug": plan["slug"], "deck": plan["deck"], **_slim_trim(plan)}


@mcp.tool()
def deck_cards(reference: str, role: str = "", card_type: str = "") -> list[dict[str, Any]]:
    """List a deck's cards, optionally filtered.

    role is one of: ramp, draw, removal, wipe, tutor, protection, counterspell,
    recursion, land. card_type matches against the type line, e.g. "Creature".
    """
    return service.deck_cards(reference, role=role, card_type=card_type)


@mcp.tool()
def deck_engine(reference: str) -> dict[str, Any]:
    """What a deck is actually built around — read this before judging any card.

    Returns the archetype, the functional clusters the deck invests in, what the
    commander's own text asks for, which cards are hardest to cast, and which
    clusters are oversupplied. A card's absence from EDHREC lists is never
    evidence of anything; these deck-internal signals are.
    """
    return service.deck_engine(reference)


@mcp.tool()
def deck_intent(reference: str) -> dict[str, Any]:
    """What this deck is declared to be about (intent.md), beside the inference.

    Use this to run the intent interview: `inferred` holds the tool's guess
    (archetype, tribe, commander role, what the 99 must supply) to pre-fill
    questions; `intent` holds what Nicolas has declared, which analysis obeys.
    """
    return service.deck_intent_show(reference)


@mcp.tool()
def deck_intent_set(reference: str, assignments: dict[str, str]) -> dict[str, Any]:
    """Write interview answers into intent.md, creating it on first use.

    assignments maps field to value: archetype, tribe, win_conditions,
    core_cards (sacred — never suggested as cuts; '+Name' adds, '-Name'
    removes), flexible_cards, core_categories ('ramp=20, draw=15'),
    budget_per_card, power_bracket, meta_notes, prose. Analysis and
    suggestions obey the result immediately.
    """
    return service.deck_intent_set(reference, assignments)


@mcp.tool()
def card_lookup(name: str) -> dict[str, Any]:
    """Look up a Magic card on Scryfall by exact name."""
    return service.card_lookup(name)


@mcp.tool()
def edhrec_commander(commander: str, limit: int = 25, theme: str = "") -> dict[str, Any]:
    """EDHREC's highest-synergy cards for a commander, with inclusion rates.

    Also returns the commander's build variants (themes, with deck counts) and
    the closest similar commanders. Pass theme (a slug from `themes`, e.g.
    "clones") to read that variant's page instead of the all-builds average.
    """
    return service.edhrec_commander(commander, limit=limit, theme=theme)


@mcp.tool()
def combo_search(card_name: str) -> list[dict[str, Any]]:
    """Find catalogued combos that use a given card, via Commander Spellbook."""
    return service.combo_search(card_name)


@mcp.tool()
def cache_status() -> dict[str, Any]:
    """Report the state of the local Scryfall card cache."""
    return service.cache_status()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
