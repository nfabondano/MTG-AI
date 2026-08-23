"""Commander Spellbook — the combo database.

EDHREC's combo JSON is gated (403), so combos come from Commander Spellbook's
public backend instead. Each "variant" lists the cards it `uses` and the
`produces` results, which lets us report both combos a deck already assembles
and combos it is one card away from — the second being some of the most
actionable suggestion material available.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..http import SourceError, get_json

BASE = "https://backend.commanderspellbook.com"


@dataclass
class Combo:
    id: str
    cards: list[str] = field(default_factory=list)
    produces: list[str] = field(default_factory=list)
    identity: str = ""
    popularity: int = 0
    bracket_tag: str = ""
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "cards": self.cards,
            "produces": self.produces,
            "identity": self.identity,
            "popularity": self.popularity,
            "bracket_tag": self.bracket_tag,
            "url": f"https://commanderspellbook.com/combo/{self.id}/",
        }


def _parse(variant: dict[str, Any]) -> Combo:
    return Combo(
        id=str(variant.get("id", "")),
        cards=[
            (u.get("card") or {}).get("name", "")
            for u in variant.get("uses") or []
            if (u.get("card") or {}).get("name")
        ],
        produces=[
            (p.get("feature") or {}).get("name", "")
            for p in variant.get("produces") or []
            if (p.get("feature") or {}).get("name")
        ],
        identity=variant.get("identity", ""),
        popularity=int(variant.get("popularity") or 0),
        bracket_tag=variant.get("bracketTag", "") or "",
        description=variant.get("description", "") or "",
    )


def combos_using(card_name: str, *, limit: int = 50) -> list[Combo]:
    """Every catalogued combo that uses a given card."""
    try:
        payload = get_json(
            f"{BASE}/variants/",
            params={"q": f'card:"{card_name}"', "limit": limit},
        )
    except SourceError:
        return []
    return [_parse(v) for v in payload.get("results") or []]


def find_in_deck(
    deck_names: set[str],
    probe_cards: list[str],
    *,
    max_probes: int = 40,
) -> dict[str, list[Combo]]:
    """Classify combos touching this deck as complete or one card away.

    `probe_cards` are the cards worth querying (querying all 100 would be slow
    and rude); the caller picks them, typically commanders plus the cards most
    likely to combo.

    Returns {"complete": [...], "near_miss": [...]}, each sorted by popularity.
    """
    normalised = {n.split("//")[0].strip().lower() for n in deck_names}
    complete: dict[str, Combo] = {}
    near: dict[str, tuple[Combo, str]] = {}

    for probe in probe_cards[:max_probes]:
        for combo in combos_using(probe):
            if not combo.cards:
                continue
            missing = [
                c for c in combo.cards
                if c.split("//")[0].strip().lower() not in normalised
            ]
            if not missing:
                complete[combo.id] = combo
            elif len(missing) == 1:
                near[combo.id] = (combo, missing[0])

    return {
        "complete": sorted(complete.values(), key=lambda c: -c.popularity),
        "near_miss": [
            _with_missing(combo, missing)
            for combo, missing in sorted(near.values(), key=lambda t: -t[0].popularity)
        ],
    }


def _with_missing(combo: Combo, missing: str) -> Combo:
    """Tag a near-miss combo with the single card that would complete it."""
    combo.description = f"Missing: {missing}"
    return combo
