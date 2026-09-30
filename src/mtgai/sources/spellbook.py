"""Commander Spellbook — the combo database.

EDHREC's combo JSON is gated (403), so combos come from Commander Spellbook's
public backend instead. Each "variant" lists the cards it `uses`, any generic
pieces it `requires` ("a Persist creature"), and the `produces` results.

The whole list goes to Spellbook's own `find-my-combos` endpoint in one
request: it returns every catalogued combo the list assembles and every combo
it is one card away from. That replaced probing the 25 most combo-likely cards
one by one, which both missed combos and — by ignoring `requires` — reported
combos the deck could not actually assemble ("Ashnod's Altar + Cathars'
Crusade" needs a Persist creature Felisa's deck does not run).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..http import SourceError, get_json, post_json

BASE = "https://backend.commanderspellbook.com"
FIND_MY_COMBOS = f"{BASE}/find-my-combos"

# find-my-combos pages its results; a real deck fits in one page, and this
# bounds the worst case.
MAX_PAGES = 5


def _normalise_identity(value: Any) -> str:
    """Spellbook writes colourless as "C"; the tool writes it as ""."""
    text = str(value or "").strip().upper()
    return "" if text in ("", "C") else text


def _key(name: str) -> str:
    return name.split("//")[0].strip().lower()


@dataclass
class Combo:
    id: str
    cards: list[str] = field(default_factory=list)
    produces: list[str] = field(default_factory=list)
    identity: str = ""
    popularity: int = 0
    bracket_tag: str = ""
    description: str = ""
    # Generic pieces the combo also needs ("Persist Creature"), by name.
    requires: list[str] = field(default_factory=list)
    # Pieces in total, commander and generic pieces included. A "two-card
    # combo" in the bracket sense is size 2.
    size: int = 0
    # For a near miss: the one named card the deck lacks.
    missing: str = ""
    # For a combo whose named cards are all present: the generic piece it
    # still needs, which the tool cannot verify.
    missing_template: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "cards": self.cards,
            "produces": self.produces,
            "identity": self.identity,
            "popularity": self.popularity,
            "bracket_tag": self.bracket_tag,
            "requires": self.requires,
            "size": self.size or len(self.cards) + len(self.requires),
            "url": f"https://commanderspellbook.com/combo/{self.id}/",
        }


def _parse(variant: dict[str, Any]) -> Combo:
    uses = variant.get("uses") or []
    requires = variant.get("requires") or []
    size = sum(int(u.get("quantity") or 1) for u in uses) + sum(
        int(r.get("quantity") or 1) for r in requires
    )
    return Combo(
        id=str(variant.get("id", "")),
        cards=[
            (u.get("card") or {}).get("name", "")
            for u in uses
            if (u.get("card") or {}).get("name")
        ],
        produces=[
            (p.get("feature") or {}).get("name", "")
            for p in variant.get("produces") or []
            if (p.get("feature") or {}).get("name")
        ],
        identity=_normalise_identity(variant.get("identity")),
        popularity=int(variant.get("popularity") or 0),
        bracket_tag=variant.get("bracketTag", "") or "",
        description=variant.get("description", "") or "",
        requires=[
            (r.get("template") or {}).get("name", "")
            for r in requires
            if (r.get("template") or {}).get("name")
        ],
        size=size,
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


def _classify(
    combo: Combo, deck_keys: set[str]
) -> str | None:
    """complete / near_miss / needs_template for one variant, or None."""
    missing = [c for c in combo.cards if _key(c) not in deck_keys]
    if not missing:
        if combo.requires:
            combo.missing_template = combo.requires[0]
            return "needs_template"
        return "complete"
    if len(missing) == 1 and not combo.requires:
        combo.missing = missing[0]
        combo.description = f"Missing: {missing[0]}"
        return "near_miss"
    return None


def find_my_combos(
    commanders: list[str],
    main: list[tuple[str, int]],
    deck_names: set[str],
) -> dict[str, list[Combo]]:
    """Every combo the list assembles or is one card from, in one request.

    The body is sorted so the same deck always hits the same cache entry.
    Spellbook's `included` is trusted as complete; `almostIncluded` is sorted
    into near misses (one named card short) and combos that only need a
    generic piece the tool cannot check.
    """
    body = {
        "commanders": [{"card": name, "quantity": 1} for name in sorted(commanders)],
        "main": [{"card": name, "quantity": qty} for name, qty in sorted(main)],
    }
    included: list[dict[str, Any]] = []
    almost: list[dict[str, Any]] = []
    url: str | None = FIND_MY_COMBOS
    for _ in range(MAX_PAGES):
        if not url:
            break
        payload = post_json(url, body)
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, dict):
            raise SourceError("unexpected find-my-combos response", url=url)
        included.extend(results.get("included") or [])
        almost.extend(results.get("almostIncluded") or [])
        url = payload.get("next")

    deck_keys = {_key(n) for n in deck_names}
    found: dict[str, list[Combo]] = {"complete": [], "near_miss": [], "needs_template": []}
    for variant in included:
        found["complete"].append(_parse(variant))
    for variant in almost:
        combo = _parse(variant)
        kind = _classify(combo, deck_keys)
        if kind in ("near_miss", "needs_template"):
            found[kind].append(combo)
    for kind in found:
        found[kind].sort(key=lambda c: -c.popularity)
    return found


def find_in_deck(
    deck_names: set[str],
    probe_cards: list[str],
    *,
    max_probes: int = 40,
) -> dict[str, list[Combo]]:
    """Classify combos touching this deck by probing cards one at a time.

    The fallback for when find-my-combos is unreachable. `probe_cards` are the
    cards worth querying; the caller picks them, typically commanders plus the
    cards most likely to combo. A variant with generic requirements is never
    "complete" — the named cards being present proves nothing about them.

    Returns {"complete", "near_miss", "needs_template"}, each sorted by popularity.
    """
    deck_keys = {_key(n) for n in deck_names}
    buckets: dict[str, dict[str, Combo]] = {
        "complete": {}, "near_miss": {}, "needs_template": {}
    }

    for probe in probe_cards[:max_probes]:
        for combo in combos_using(probe):
            if not combo.cards:
                continue
            kind = _classify(combo, deck_keys)
            if kind:
                buckets[kind][combo.id] = combo

    return {
        kind: sorted(combos.values(), key=lambda c: -c.popularity)
        for kind, combos in buckets.items()
    }
