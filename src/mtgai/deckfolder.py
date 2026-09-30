"""One folder per deck, committed to the repository.

Committing the folders is what makes the workflow portable: every Claude Code
session, on any device, sees the same decks and the same analysis without
needing network access or a warm cache.

`notes.md` is yours. It is created once, empty, and never written again by this
tool — it is the one file where thinking accumulates across sessions.
"""

from __future__ import annotations

import json
import re
import shutil
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import decks_dir
from .intent import DeckIntent, parse_intent, render_intent
from .model import Deck

SOURCE = "source.json"
DECK = "deck.json"
DECKLIST = "deck.txt"
ANALYSIS = "analysis.md"
SUGGESTIONS = "suggestions.md"
ENGINE = "engine.md"
NOTES = "notes.md"
INTENT = "intent.md"

NOTES_TEMPLATE = """# Notes

Your space. Nothing in this repository will overwrite this file.

## Ideas

## Cards I'm considering

## Cards I refuse to cut
"""


def slugify(name: str, deck_id: int) -> str:
    """A stable, unique folder name: deck name plus the Archidekt id."""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    text = re.sub(r"-{2,}", "-", text)[:60].strip("-")
    return f"{text}-{deck_id}" if text else str(deck_id)


@dataclass
class DeckFolder:
    slug: str
    path: Path

    @property
    def source_path(self) -> Path:
        return self.path / SOURCE

    @property
    def deck_path(self) -> Path:
        return self.path / DECK

    @property
    def analysis_path(self) -> Path:
        return self.path / ANALYSIS

    @property
    def suggestions_path(self) -> Path:
        return self.path / SUGGESTIONS

    @property
    def engine_path(self) -> Path:
        return self.path / ENGINE

    @property
    def notes_path(self) -> Path:
        return self.path / NOTES

    @property
    def intent_path(self) -> Path:
        return self.path / INTENT

    def exists(self) -> bool:
        return self.deck_path.exists()

    def ensure(self) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        # Created once and then left alone, so a re-import never destroys notes.
        if not self.notes_path.exists():
            self.notes_path.write_text(NOTES_TEMPLATE)

    def write_source(self, payload: dict[str, Any]) -> None:
        self.ensure()
        self.source_path.write_text(json.dumps(payload, indent=1, ensure_ascii=False))

    def write_deck(self, deck: Deck) -> None:
        self.ensure()
        self.deck_path.write_text(json.dumps(deck.to_dict(), indent=1, ensure_ascii=False))
        self.path.joinpath(DECKLIST).write_text(render_decklist(deck))

    def read_deck(self) -> Deck:
        if not self.deck_path.exists():
            raise FileNotFoundError(
                f"no deck at {self.deck_path} — run `mtg deck add` first"
            )
        return Deck.from_dict(json.loads(self.deck_path.read_text()))

    def read_source(self) -> dict[str, Any]:
        return json.loads(self.source_path.read_text())

    def write_analysis(self, markdown: str) -> None:
        self.ensure()
        self.analysis_path.write_text(markdown)

    def write_suggestions(self, markdown: str) -> None:
        self.ensure()
        self.suggestions_path.write_text(markdown)

    # The generated marker's absence means a person has edited this file.
    ENGINE_MARKER = "<!-- generated -->"

    def write_engine(self, markdown: str) -> None:
        """Write the inferred engine, unless it has been corrected by hand.

        The inference is a starting point. Once Nicolas edits `engine.md` the
        correction is authoritative and re-analysis must not clobber it — the
        tool's guess is worth less than someone who knows what the deck does.
        """
        self.ensure()
        if self.engine_path.exists():
            existing = self.engine_path.read_text()
            if self.ENGINE_MARKER not in existing:
                return
        self.engine_path.write_text(f"{self.ENGINE_MARKER}\n{markdown}")

    def engine_is_user_edited(self) -> bool:
        return (
            self.engine_path.exists()
            and self.ENGINE_MARKER not in self.engine_path.read_text()
        )

    def read_intent(self) -> tuple[DeckIntent, list[str]] | None:
        """The declared intent and its parse warnings, or None if never written."""
        if not self.intent_path.exists():
            return None
        return parse_intent(self.intent_path.read_text())

    def write_intent(self, intent: DeckIntent, *, only_if_absent: bool = False) -> bool:
        """Write intent.md. With only_if_absent, an existing file always wins.

        Analysis and refresh use only_if_absent — like notes.md, intent.md
        belongs to Nicolas once it exists, and only the explicit intent
        commands may rewrite it.
        """
        self.ensure()
        if only_if_absent and self.intent_path.exists():
            return False
        self.intent_path.write_text(render_intent(intent))
        return True


def render_decklist(deck: Deck) -> str:
    """A plain decklist that pastes straight back into Archidekt."""
    lines = [f"// {deck.name}", f"// {deck.url}", ""]

    commanders = deck.commanders
    if commanders:
        lines.append("// Commander")
        for card in commanders:
            lines.append(f"{card.quantity} {card.name}")
        lines.append("")

    lines.append("// Deck")
    for card in sorted(deck.cards, key=lambda c: (c.primary_type, c.name)):
        if card.is_commander:
            continue
        lines.append(f"{card.quantity} {card.name}")

    if deck.excluded:
        lines.append("")
        lines.append("// Maybeboard (not counted in the deck)")
        for entry in deck.excluded:
            lines.append(f"{entry['quantity']} {entry['name']}")

    return "\n".join(lines) + "\n"


def folder_for(slug: str) -> DeckFolder:
    return DeckFolder(slug=slug, path=decks_dir() / slug)


def resolve(reference: str) -> DeckFolder:
    """Find a deck folder by slug, by Archidekt id, or by a fragment of its name."""
    root = decks_dir()
    candidate = folder_for(reference)
    if candidate.exists():
        return candidate

    if not root.exists():
        raise FileNotFoundError("no decks imported yet — run `mtg deck add <url>`")

    needle = reference.lower()
    matches = [
        DeckFolder(slug=p.name, path=p)
        for p in sorted(root.iterdir())
        if p.is_dir() and (p / DECK).exists() and needle in p.name.lower()
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = ", ".join(m.slug for m in matches)
        raise ValueError(f"{reference!r} matches several decks: {names}")
    raise FileNotFoundError(f"no deck matching {reference!r}")


def all_folders() -> list[DeckFolder]:
    root = decks_dir()
    if not root.exists():
        return []
    return [
        DeckFolder(slug=p.name, path=p)
        for p in sorted(root.iterdir())
        if p.is_dir() and (p / DECK).exists()
    ]


def find_by_id(deck_id: int) -> DeckFolder | None:
    """The folder already tracking an Archidekt id, whatever its name now."""
    for folder in all_folders():
        if folder.slug == str(deck_id) or folder.slug.endswith(f"-{deck_id}"):
            return folder
    return None


# Rebuilt from Archidekt on every import, so they never need carrying over.
_GENERATED = frozenset({SOURCE, DECK, DECKLIST, ANALYSIS, SUGGESTIONS})


def _is_template(text: str) -> bool:
    return not text.strip() or text.strip() == NOTES_TEMPLATE.strip()


def carry_over(old: DeckFolder, new: DeckFolder) -> dict[str, list[str]]:
    """Move what belongs to Nicolas from a deck's old folder into its new one.

    A deck that is renamed on Archidekt, or rebuilt under a new link, gets a
    new folder; without this its notes, its declared intent and a corrected
    engine.md stayed behind in a folder nothing reads any more.

    Generated files stay behind — they are rebuilt. Nothing already in the
    new folder is overwritten: notes are appended under a heading (the empty
    template is neither carried nor kept in the way), and when both folders
    hold a different intent.md, a different hand-edited engine.md or any
    other file of the same name, both are kept and the clash is reported as a
    conflict, which also keeps the old folder from being removed.
    """
    new.ensure()
    outcome: dict[str, list[str]] = {"copied": [], "appended": [], "skipped": [], "conflicts": []}
    for path in sorted(old.path.iterdir()):
        name = path.name
        target = new.path / name
        if name in _GENERATED:
            continue
        if name == NOTES:
            text = path.read_text()
            current = target.read_text() if target.exists() else ""
            if _is_template(text) or text.strip() in current:
                outcome["skipped"].append(name)
            elif _is_template(current):
                target.write_text(text)
                outcome["copied"].append(name)
            else:
                with target.open("a") as handle:
                    handle.write(f"\n\n## Carried over from {old.slug}\n\n{text.strip()}\n")
                outcome["appended"].append(name)
            continue
        if name == ENGINE and old.ENGINE_MARKER in path.read_text():
            continue  # generated, and regenerated in the new folder
        if path.is_dir():
            if target.exists():
                outcome["conflicts"].append(name)
            else:
                shutil.copytree(path, target)
                outcome["copied"].append(name)
            continue
        replaceable = (
            name == ENGINE and target.exists() and new.ENGINE_MARKER in target.read_text()
        )
        if not target.exists() or replaceable:
            shutil.copy2(path, target)
            outcome["copied"].append(name)
        elif target.read_bytes() == path.read_bytes():
            outcome["skipped"].append(name)
        else:
            outcome["conflicts"].append(name)
    return outcome


def remove(folder: DeckFolder) -> None:
    """Delete a deck folder — only ever a deck folder directly inside decks/."""
    root = decks_dir().resolve()
    path = folder.path.resolve()
    if path.parent != root or not path.is_dir() or not (
        (path / DECK).exists() or (path / SOURCE).exists()
    ):
        raise ValueError(f"refusing to remove {path}: not a deck folder inside {root}")
    shutil.rmtree(path)


def summarise(deck: Deck) -> dict[str, Any]:
    """The slim view used by `deck list` and `deck show`.

    Deliberately small: reading the whole deck.json into a session costs far
    more context than a question about a deck usually needs.
    """
    return {
        "slug": deck.slug,
        "name": deck.name,
        "commanders": [c.name for c in deck.commanders],
        "format": deck.format_name,
        "color_identity": deck.color_identity(),
        "total_cards": deck.total_cards,
        "lands": sum(c.quantity for c in deck.lands),
        "url": deck.url,
        "updated_at": deck.updated_at,
        "imported_at": deck.imported_at,
        "maybeboard": len(deck.excluded),
    }


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
