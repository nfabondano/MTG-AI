"""Command line interface — the engine everything else calls.

Every command takes `--json` so a Claude session can consume structured output
instead of parsing prose.
"""

from __future__ import annotations

import json as jsonlib
import sys
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from . import service
from .http import SourceError

app = typer.Typer(
    help="Analyse Archidekt decks against Scryfall, EDHREC and Commander Spellbook.",
    no_args_is_help=True,
    add_completion=False,
)
deck_app = typer.Typer(help="Import and analyse decks.", no_args_is_help=True)
cache_app = typer.Typer(help="Manage the local Scryfall card cache.", no_args_is_help=True)
app.add_typer(deck_app, name="deck")
app.add_typer(cache_app, name="cache")

console = Console()
err_console = Console(stderr=True)


def _emit(payload: Any) -> None:
    console.print_json(jsonlib.dumps(payload, default=str))


def _fail(message: str) -> None:
    err_console.print(f"[red]Error:[/red] {message}")
    raise typer.Exit(code=1)


@deck_app.command("add")
def deck_add(
    reference: str = typer.Argument(..., help="Archidekt deck URL or id."),
    offline: bool = typer.Option(False, "--offline", help="Skip EDHREC, combos and prices."),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Import a deck from Archidekt, then analyse it."""
    try:
        result = service.add_deck(reference, offline=offline)
    except (SourceError, ValueError) as exc:
        _fail(str(exc))
        return

    if json_out:
        _emit({"slug": result["slug"], "deck": result["deck"], "headline": result["analysis"]["headline"]})
        return

    summary = result["deck"]
    console.print(f"[bold green]Imported[/bold green] {summary['name']}")
    console.print(f"  slug      {result['slug']}")
    console.print(f"  commander {', '.join(summary['commanders']) or '—'}")
    console.print(f"  cards     {summary['total_cards']} ({summary['lands']} lands)")
    console.print(f"  folder    {result['path']}")
    console.print()
    _print_headline(result["analysis"])


@deck_app.command("refresh")
def deck_refresh(
    reference: str = typer.Argument(..., help="Deck slug, id or name fragment."),
    offline: bool = typer.Option(False, "--offline"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Re-pull a deck from Archidekt and re-analyse it."""
    try:
        result = service.refresh_deck(reference, offline=offline)
    except (SourceError, ValueError, FileNotFoundError) as exc:
        _fail(str(exc))
        return

    if json_out:
        _emit({"slug": result["slug"], "deck": result["deck"]})
        return
    console.print(f"[bold green]Refreshed[/bold green] {result['deck']['name']}")
    _print_headline(result["analysis"])


@deck_app.command("analyze")
def deck_analyze(
    reference: str = typer.Argument(..., help="Deck slug, id or name fragment."),
    offline: bool = typer.Option(False, "--offline"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Regenerate analysis.md from the stored deck."""
    try:
        result = service.analyse_deck(reference, offline=offline)
    except (SourceError, ValueError, FileNotFoundError) as exc:
        _fail(str(exc))
        return

    if json_out:
        _emit(result)
        return
    console.print(f"[bold green]Analysed[/bold green] {result['deck']['name']}")
    _print_headline(result)


@deck_app.command("suggest")
def deck_suggest(
    reference: str = typer.Argument(..., help="Deck slug, id or name fragment."),
    budget: float = typer.Option(None, "--budget", help="Ignore suggestions above this USD price."),
    offline: bool = typer.Option(False, "--offline"),
) -> None:
    """Regenerate suggestions.md."""
    try:
        markdown = service.suggest(reference, budget=budget, offline=offline)
    except (SourceError, ValueError, FileNotFoundError) as exc:
        _fail(str(exc))
        return
    console.print(markdown)


@deck_app.command("list")
def deck_list(json_out: bool = typer.Option(False, "--json")) -> None:
    """List every tracked deck."""
    decks = service.list_decks()
    if json_out:
        _emit(decks)
        return
    if not decks:
        console.print("No decks yet. Import one with [bold]mtg deck add <url>[/bold].")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Slug")
    table.add_column("Deck")
    table.add_column("Commander")
    table.add_column("Colours")
    table.add_column("Cards", justify="right")
    for entry in decks:
        table.add_row(
            entry["slug"],
            entry["name"],
            ", ".join(entry["commanders"]) or "—",
            "/".join(entry["color_identity"]) or "—",
            str(entry["total_cards"]),
        )
    console.print(table)


@deck_app.command("show")
def deck_show(
    reference: str = typer.Argument(..., help="Deck slug, id or name fragment."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Show a slim summary of one deck."""
    try:
        summary = service.show_deck(reference)
    except (ValueError, FileNotFoundError) as exc:
        _fail(str(exc))
        return

    if json_out:
        _emit(summary)
        return
    console.print(f"[bold]{summary['name']}[/bold]  ({summary['slug']})")
    console.print(f"  commander {', '.join(summary['commanders']) or '—'}")
    console.print(f"  colours   {'/'.join(summary['color_identity']) or '—'}")
    console.print(f"  cards     {summary['total_cards']} ({summary['lands']} lands)")
    console.print(f"  archidekt {summary['url']}")
    console.print()
    roles = summary.get("roles") or {}
    console.print("  " + " · ".join(f"{k} {v}" for k, v in roles.items() if v))
    console.print()
    console.print(f"  analysis  {summary['files']['analysis']}")


@deck_app.command("cards")
def deck_cards(
    reference: str = typer.Argument(..., help="Deck slug, id or name fragment."),
    role: str = typer.Option("", "--role", help="Filter by role, e.g. ramp, draw, removal."),
    card_type: str = typer.Option("", "--type", help="Filter by type line substring."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """List cards in a deck, optionally filtered by role or type."""
    try:
        cards = service.deck_cards(reference, role=role, card_type=card_type)
    except (ValueError, FileNotFoundError) as exc:
        _fail(str(exc))
        return

    if json_out:
        _emit(cards)
        return
    if not cards:
        console.print("No cards match that filter.")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("Card")
    table.add_column("Cost")
    table.add_column("Type")
    table.add_column("Roles")
    for card in cards:
        table.add_row(
            card["name"],
            card["mana_cost"] or "—",
            card["type_line"][:28],
            ", ".join(card["roles"]) or "—",
        )
    console.print(table)


@app.command("card")
def card(
    name: str = typer.Argument(..., help="Exact card name."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Look up a card on Scryfall."""
    data = service.card_lookup(name)
    if json_out:
        _emit(data)
        return
    if not data["found"]:
        _fail(f"no card named {name!r}")
        return
    console.print(f"[bold]{data['name']}[/bold]  {data['mana_cost'] or ''}")
    console.print(f"  {data['type_line']}")
    console.print()
    console.print(f"  {data['oracle_text']}")
    console.print()
    console.print(
        f"  commander: {data['legalities']['commander']} · "
        f"edhrec rank: {data['edhrec_rank']} · ${data['price_usd'] or '—'}"
    )


@app.command("edhrec")
def edhrec_cmd(
    commander: str = typer.Argument(..., help="Commander name."),
    limit: int = typer.Option(25, "--limit"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Show EDHREC's top recommendations for a commander."""
    data = service.edhrec_commander(commander, limit=limit)
    if json_out:
        _emit(data)
        return
    if not data["found"]:
        _fail(data.get("reason") or "no EDHREC data")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("Card")
    table.add_column("Synergy", justify="right")
    table.add_column("Inclusion", justify="right")
    for rec in data["recommendations"]:
        table.add_row(rec["name"], f"{rec['synergy']:+.2f}", f"{rec['inclusion']:.0%}")
    console.print(table)


@app.command("combos")
def combos_cmd(
    card_name: str = typer.Argument(..., help="Card name to search combos for."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Find catalogued combos using a card."""
    results = service.combo_search(card_name)
    if json_out:
        _emit(results)
        return
    if not results:
        console.print(f"No combos found for {card_name!r}.")
        return
    for combo in results:
        console.print(f"[bold]{' + '.join(combo['cards'])}[/bold]")
        console.print(f"  → {', '.join(combo['produces'][:3])}")


@cache_app.command("refresh")
def cache_refresh(
    force: bool = typer.Option(False, "--force", help="Re-download even if current."),
) -> None:
    """Download Scryfall's Oracle Cards bulk data into the local cache."""
    console.print("Refreshing Scryfall cache…")
    try:
        result = service.cache_refresh(force=force)
    except SourceError as exc:
        _fail(str(exc))
        return
    if result.get("refreshed"):
        console.print(f"[green]Cached {result['cards']:,} cards[/green] ({result['updated_at']})")
    else:
        console.print(f"Already current: {result['cards']:,} cards ({result['updated_at']})")


@cache_app.command("status")
def cache_status(json_out: bool = typer.Option(False, "--json")) -> None:
    """Show the state of the local card cache."""
    status = service.cache_status()
    if json_out:
        _emit(status)
        return
    if not status["present"]:
        console.print("No cache. Run [bold]mtg cache refresh[/bold].")
        return
    console.print(f"{status['cards']:,} cards cached (updated {status['updated_at']})")


def _print_headline(result: dict[str, Any]) -> None:
    headline = result.get("headline") or []
    if not headline:
        console.print("[green]Nothing structural to flag.[/green]")
        return
    console.print("[bold]What stands out:[/bold]")
    for line in headline:
        console.print(f"  • {line}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
