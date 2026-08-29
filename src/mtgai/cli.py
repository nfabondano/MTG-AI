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
    loose: bool = typer.Option(
        False, "--loose", help="Also show weak candidates the tool cannot ground."
    ),
) -> None:
    """Regenerate suggestions.md."""
    try:
        markdown = service.suggest(reference, budget=budget, offline=offline, loose=loose)
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
    console.print(f"  does      {summary.get('archetype', '—')}")
    console.print()
    roles = summary.get("roles") or {}
    console.print("  " + " · ".join(f"{k} {v}" for k, v in roles.items() if v))
    console.print()
    console.print(f"  analysis  {summary['files']['analysis']}")


@deck_app.command("engine")
def deck_engine(
    reference: str = typer.Argument(..., help="Deck slug, id or name fragment."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Show what a deck is built around — read this before judging any card."""
    try:
        data = service.deck_engine(reference)
    except (ValueError, FileNotFoundError) as exc:
        _fail(str(exc))
        return

    if json_out:
        _emit(data)
        return

    console.print(f"[bold]{data['archetype']}[/bold]")
    if data.get("user_edited"):
        console.print("[dim](engine.md has been edited by hand — that version wins)[/dim]")
    console.print()

    wants = set(data.get("commander_wants") or [])
    clusters = data.get("clusters") or {}
    if clusters:
        console.print("  clusters")
        for name, count in list(clusters.items())[:8]:
            mark = " *" if name in wants else ""
            console.print(f"    {name:<16} {count}{mark}")
        console.print("    [dim]* the commander's own text asks for this[/dim]")
        console.print()

    strain = data.get("castability_cuts") or []
    if strain:
        console.print("  hardest to cast")
        for entry in strain[:5]:
            note = " (sole reason that requirement is high)" if entry["sole_driver"] else ""
            console.print(f"    {entry['name']} {entry['mana_cost']}{note}")
            console.print(f"      [dim]{entry['reasons'][0]}[/dim]")
        console.print()

    over = data.get("oversupplied") or []
    if over:
        console.print(
            "  oversupplied  "
            + ", ".join(f"{e['category']} {e['count']}/{e['target_high']}" for e in over[:4])
        )


@deck_app.command("intent")
def deck_intent(
    reference: str = typer.Argument(..., help="Deck slug, id or name fragment."),
    init: bool = typer.Option(
        False, "--init", help="Seed intent.md from inference and Archidekt metadata."
    ),
    force: bool = typer.Option(False, "--force", help="Allow --init to reseed an existing file."),
    set_values: list[str] = typer.Option(
        None,
        "--set",
        help="key=value edit, repeatable. Lists take 'a, b', '+Name' or '-Name'.",
    ),
    check: bool = typer.Option(False, "--check", help="Parse intent.md and report warnings."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Show or edit what this deck is declared to be about (intent.md).

    Analysis and suggestions obey the declared intent: core cards are never
    offered as cuts, core category targets replace the generic ones, and the
    declared archetype outranks the inferred one.
    """
    try:
        if init:
            data = service.deck_intent_init(reference, force=force)
        elif set_values:
            assignments: dict[str, str] = {}
            for pair in set_values:
                key, sep, value = pair.partition("=")
                if not sep:
                    _fail(f"--set takes key=value, got {pair!r}")
                    return
                assignments[key.strip()] = value
            data = service.deck_intent_set(reference, assignments)
        else:
            data = service.deck_intent_show(reference)
    except (ValueError, FileNotFoundError) as exc:
        _fail(str(exc))
        return

    if json_out:
        _emit(data)
        return

    if not data["exists"]:
        console.print("[yellow]No intent.md yet.[/yellow] The tool is guessing:")
        inferred = data["inferred"]
        console.print(f"  archetype {inferred['archetype']}")
        if inferred.get("tribe"):
            console.print(f"  tribe     {inferred['tribe']}")
        console.print(f"  commander {', '.join(inferred['commander_role'])}")
        for supply in inferred.get("supplies") or []:
            console.print(f"    needs {supply}")
        if data["archidekt"]["description"]:
            console.print("  [dim]Archidekt description found — --init will import it.[/dim]")
        console.print(
            f"\nRun [bold]mtg deck intent {reference} --init[/bold] to create it, "
            "then edit or --set."
        )
        return

    intent_data = data["intent"]
    console.print(f"[bold]{intent_data.get('archetype') or '(no declared archetype)'}[/bold]")
    if intent_data.get("tribe"):
        console.print(f"  tribe     {intent_data['tribe']}")
    if intent_data.get("commander_role"):
        console.print(f"  commander {', '.join(intent_data['commander_role'])}")
    if intent_data.get("win_conditions"):
        console.print(f"  wins by   {'; '.join(intent_data['win_conditions'])}")
    if intent_data.get("core_cards"):
        console.print(f"  sacred    {', '.join(intent_data['core_cards'])}")
    if intent_data.get("core_categories"):
        targets = ", ".join(f"{k}={v}" for k, v in intent_data["core_categories"].items())
        console.print(f"  targets   {targets}")
    if intent_data.get("budget_per_card") is not None:
        console.print(f"  budget    ${intent_data['budget_per_card']:g}/card")
    console.print(f"  [dim]{data['path']} (source: {intent_data.get('source', '?')})[/dim]")
    if check or data.get("warnings"):
        for warning in data.get("warnings") or []:
            console.print(f"  [yellow]warning:[/yellow] {warning}")


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

    # Functional tags are what let the tool see what a card does rather than
    # what its text says, so they refresh alongside the cards.
    console.print("Refreshing functional tags…")
    try:
        tags_result = service.tags_refresh(force=force)
    except SourceError as exc:
        console.print(f"[yellow]Tags unavailable:[/yellow] {exc}")
        return
    console.print(
        f"[green]{tags_result['taggings']:,} taggings[/green] across "
        f"{tags_result['tags']:,} tags"
    )


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
