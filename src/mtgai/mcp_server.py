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

mcp = MCPServer(
    "mtg-ai",
    instructions=(
        "Analyse Magic: The Gathering Commander decks imported from Archidekt. "
        "Deck folders live in decks/ and are committed, so decks stay available "
        "across sessions and devices."
    ),
)


@mcp.tool()
def deck_add(reference: str) -> dict[str, Any]:
    """Import an Archidekt deck (URL or id), analyse it, and file it in decks/.

    Returns a summary plus the headline findings. The full report is written to
    the deck's folder as analysis.md.
    """
    result = service.add_deck(reference)
    return {
        "slug": result["slug"],
        "path": result["path"],
        "deck": result["deck"],
        "headline": result["analysis"]["headline"],
    }


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
def deck_analyze(reference: str, offline: bool = False) -> dict[str, Any]:
    """Re-run the full analysis on a stored deck and rewrite analysis.md.

    Set offline=True to skip EDHREC, combos and prices.
    """
    return service.analyse_deck(reference, offline=offline)


@mcp.tool()
def deck_suggest(
    reference: str, budget: float | None = None, loose: bool = False
) -> str:
    """Generate cut and add suggestions for a deck, as markdown.

    Every cut is backed by deck-internal evidence: castability, cluster
    oversupply, curve, or doing nothing the deck is built around. budget caps
    suggested cards at that USD price; loose also surfaces weak candidates.
    """
    return service.suggest(reference, budget=budget, loose=loose)


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
