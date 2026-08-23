"""EDHREC meta statistics.

EDHREC has no official API, but the site's own front end reads JSON from
json.edhrec.com and that is open. Several paths under it (combos, themes) return
403; those are gated and retrying does not help, so every helper here degrades
to an empty result rather than failing an analysis.

The useful numbers per card are `synergy` (how much more often this card shows
up with this commander than with decks of the same colours) and the pair
`num_decks` / `potential_decks`, whose ratio is the inclusion rate.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from ..http import Forbidden, NotFound, SourceError, get_json

BASE = "https://json.edhrec.com/pages"


def slugify(name: str) -> str:
    """Convert a card name to an EDHREC slug.

    Apostrophes vanish rather than becoming separators, which is what makes
    "Atraxa, Praetor's Voice" resolve to `atraxa-praetors-voice`.
    """
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.split("//")[0].strip().lower()
    text = text.replace("'", "").replace("’", "")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


@dataclass
class Recommendation:
    """One card as EDHREC reports it for a commander."""

    name: str
    synergy: float = 0.0
    num_decks: int = 0
    potential_decks: int = 0
    list_name: str = ""

    @property
    def inclusion(self) -> float:
        """Share of this commander's decks that play the card, 0.0-1.0."""
        if not self.potential_decks:
            return 0.0
        return self.num_decks / self.potential_decks

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "synergy": round(self.synergy, 4),
            "inclusion": round(self.inclusion, 4),
            "num_decks": self.num_decks,
            "potential_decks": self.potential_decks,
            "list": self.list_name,
        }


@dataclass
class CommanderData:
    slug: str
    found: bool = False
    error: str = ""
    recommendations: list[Recommendation] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.recommendations is None:
            self.recommendations = []

    def by_name(self) -> dict[str, Recommendation]:
        """Highest-synergy entry per card; a card can appear in several lists."""
        best: dict[str, Recommendation] = {}
        for rec in self.recommendations:
            key = rec.name.split("//")[0].strip().lower()
            if key not in best or rec.synergy > best[key].synergy:
                best[key] = rec
        return best


def _parse_cardlists(payload: dict[str, Any], slug: str) -> CommanderData:
    data = CommanderData(slug=slug, found=True)
    container = payload.get("container") or {}
    cardlists = (container.get("json_dict") or {}).get("cardlists") or []
    for cardlist in cardlists:
        header = cardlist.get("header") or cardlist.get("tag") or ""
        for view in cardlist.get("cardviews") or []:
            name = view.get("name")
            if not name:
                continue
            data.recommendations.append(
                Recommendation(
                    name=name,
                    synergy=float(view.get("synergy") or 0.0),
                    num_decks=int(view.get("num_decks") or 0),
                    potential_decks=int(view.get("potential_decks") or 0),
                    list_name=header,
                )
            )
    return data


def commander(name_or_slug: str) -> CommanderData:
    """Fetch a commander's recommendation page.

    Never raises: an unknown commander or a gated endpoint comes back as
    `found=False` with a reason, so analysis continues without EDHREC data.
    """
    slug = name_or_slug if "-" in name_or_slug and " " not in name_or_slug else slugify(name_or_slug)
    try:
        payload = get_json(f"{BASE}/commanders/{slug}.json")
    except NotFound:
        return CommanderData(slug=slug, found=False, error="no EDHREC page for this commander")
    except Forbidden:
        return CommanderData(slug=slug, found=False, error="EDHREC endpoint is gated (403)")
    except SourceError as exc:
        return CommanderData(slug=slug, found=False, error=str(exc))
    return _parse_cardlists(payload, slug)


def commander_for(names: list[str]) -> CommanderData:
    """Resolve a commander page for one or more commanders.

    Partner and background pairs live at a combined slug; if that is missing,
    fall back to the first commander alone.
    """
    names = [n for n in names if n]
    if not names:
        return CommanderData(slug="", found=False, error="deck has no commander")

    if len(names) > 1:
        combined = "-".join(slugify(n) for n in sorted(names))
        data = commander(combined)
        if data.found:
            return data
    return commander(names[0])


def average_deck(name_or_slug: str) -> list[str]:
    """EDHREC's precomputed average decklist for a commander."""
    slug = name_or_slug if "-" in name_or_slug and " " not in name_or_slug else slugify(name_or_slug)
    try:
        payload = get_json(f"{BASE}/average-decks/{slug}.json")
    except SourceError:
        return []
    deck = payload.get("deck")
    if isinstance(deck, list):
        return [str(entry) for entry in deck]
    parsed = _parse_cardlists(payload, slug)
    return [r.name for r in parsed.recommendations]


def card(name: str) -> dict[str, Any]:
    """Per-card EDHREC data (inclusion counts, salt score)."""
    try:
        payload = get_json(f"{BASE}/cards/{slugify(name)}.json")
    except SourceError:
        return {}
    return (payload.get("container") or {}).get("json_dict", {}).get("card", {}) or {}
