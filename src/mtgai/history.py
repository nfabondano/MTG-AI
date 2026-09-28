"""What changed between syncs, and which suggested cuts Nicolas turned down.

A refresh is the one moment the tool sees Nicolas's decisions. Cards he added
since the last sync are a choice he just made. Cards the tool suggested
cutting, and that he kept while cutting others, are advice he looked at and
declined. Re-offering either on soft evidence — a crowded cluster, a high
curve, no engine role — is the tool arguing with a decision already taken. It
did exactly that with Slinza: Garruk, Curse Breaker was offered as a cut the
day it was added, and Radagast of Rhosgobel a second time after being kept.

Hard evidence still applies to these cards (colour identity, castability), and
listing one under `flexible_cards` in intent.md reopens it.

The record lives in deck.json under `history`:

- `added` / `removed` — what the latest change to the list did
- `kept_after_suggested_cut` — name → date it was kept, carried forward while
  the card stays in the deck
- `suggested_cuts` — the cuts the tool last wrote to suggestions.md, which the
  next refresh compares against
"""

from __future__ import annotations

import re
from typing import Any

from .model import Deck

JUST_ADDED = "just added"
KEPT = "kept after an earlier suggested cut"


def key(name: str) -> str:
    """Front face, lowercased — how cards are matched across syncs."""
    return name.split("//")[0].strip().lower()


def is_soft(evidence: str) -> bool:
    """Evidence that is judgement — the kind a decision to keep a card answers.

    Colour identity and castability are facts about the deck, and stay in
    force whatever Nicolas decided; popularity was never evidence at all.
    """
    return evidence.startswith("oversupplied") or evidence in {
        "curve",
        "no engine participation",
    }


def derive(
    previous: Deck | None,
    current: Deck,
    previous_cuts: list[str],
    *,
    today: str = "",
) -> dict[str, Any]:
    """The history a freshly synced deck should carry.

    A card still in the deck after it was suggested as a cut counts as kept
    only when this sync removed something: Nicolas was cutting, and chose other
    cards. A sync that only added cards says nothing about the suggestions yet.
    A first import has no history — nothing in it was a decision against advice.
    """
    if previous is None:
        return {}
    # The Archidekt edit that showed the decision; today only as a fallback.
    since = (current.updated_at or "")[:10] or today
    before = previous.history or {}
    old = {key(c.name): c.name for c in previous.cards}
    new = {key(c.name): c.name for c in current.cards}
    added = sorted(new[k] for k in new.keys() - old.keys())
    removed = sorted(old[k] for k in old.keys() - new.keys())

    kept = {
        name: since
        for name, since in (before.get("kept_after_suggested_cut") or {}).items()
        if key(name) in new
    }
    if removed:
        already = {key(name) for name in kept}
        for name in previous_cuts:
            if key(name) in new and key(name) not in already:
                kept[new[key(name)]] = since

    if not added and not removed:
        # Nothing moved: the last real change is still the latest one.
        added = list(before.get("added") or [])
        removed = list(before.get("removed") or [])

    return {
        "previous_version": previous.updated_at,
        "added": added,
        "removed": removed,
        "kept_after_suggested_cut": kept,
        "suggested_cuts": list(previous_cuts),
    }


def record_cuts(deck: Deck, cuts: list[dict[str, Any]]) -> bool:
    """Remember the soft-evidence cuts just offered, for the next refresh.

    Returns whether the record changed, so callers can skip rewriting deck.json.
    """
    offered = [c["name"] for c in cuts if is_soft(c.get("evidence") or "")]
    before = deck.history or {}
    if before.get("suggested_cuts") == offered:
        return False
    deck.history = {**before, "suggested_cuts": offered}
    return True


def deliberate(deck: Deck, flexible: set[str] | None = None) -> dict[str, str]:
    """Cards Nicolas has decided on since the tool last advised: key → why.

    `flexible` (front-face keys from intent.md) reopens a card to suggestions.
    """
    history = deck.history or {}
    present = {key(c.name) for c in deck.cards}
    found: dict[str, str] = {}
    for name in history.get("added") or []:
        found[key(name)] = JUST_ADDED
    for name in history.get("kept_after_suggested_cut") or {}:
        found[key(name)] = KEPT
    flexible = flexible or set()
    return {k: why for k, why in found.items() if k in present and k not in flexible}


# The layout report.render_suggestions writes: a bold name, the reason, then
# the evidence in italics on the next line. Read back only for decks whose
# deck.json predates `history` — from then on the cuts are recorded directly.
_CUT_SECTION_RE = re.compile(r"^## Consider cutting\n(.*?)(?=^## |^---|\Z)", re.M | re.S)
_CUT_ENTRY_RE = re.compile(r"^- \*\*(.+?)\*\* — .*\n\s+_\((.+?)\)_", re.M)


def parse_suggested_cuts(markdown: str) -> list[str]:
    """The soft-evidence cuts under "## Consider cutting" in a suggestions.md."""
    section = _CUT_SECTION_RE.search(markdown or "")
    if not section:
        return []
    return [
        name for name, evidence in _CUT_ENTRY_RE.findall(section.group(1)) if is_soft(evidence)
    ]


def previous_suggested_cuts(previous: Deck | None, suggestions_md: str | None) -> list[str]:
    """The cuts offered before this sync: recorded, or read from the old report."""
    if previous is not None and "suggested_cuts" in (previous.history or {}):
        return list(previous.history["suggested_cuts"])
    return parse_suggested_cuts(suggestions_md or "")
