"""Archidekt deck ingestion.

Archidekt's API is unofficial but open: GET /api/decks/{id}/ returns the whole
deck without authentication, including a Scryfall id per card. No browser
automation is needed.

Two details in the payload are easy to get wrong and both silently corrupt the
analysis:

1. Categories carry an `includedInDeck` flag. A maybeboard card is tagged with
   *both* `Maybeboard` and its type category (verified: `['Maybeboard',
   'Artifact']`), so a card must be excluded when **any** of its categories is
   flagged out, not when all of them are.
2. `card.uid` is the Scryfall *printing* id while `card.oracleCard.uid` is the
   *oracle* id. They are different values and joining on the wrong one fails
   without an error.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from ..http import get_json
from ..model import ARCHIDEKT_COLOR_MAP, COLORS, CardEntry, Deck
from . import scryfall

API = "https://archidekt.com/api/decks"

DECK_FORMATS = {
    1: "Standard",
    2: "Modern",
    3: "Commander",
    4: "Legacy",
    5: "Vintage",
    6: "Pauper",
    7: "Custom",
    8: "Frontier",
    9: "Future Standard",
    10: "Penny Dreadful",
    11: "1v1 Commander",
    12: "Duel Commander",
    13: "Brawl",
    14: "Oathbreaker",
    15: "Pioneer",
    16: "Historic",
    17: "Pauper EDH",
    18: "Alchemy",
    19: "Explorer",
    20: "Historic Brawl",
    21: "Gladiator",
    22: "Premodern",
    23: "Predh",
    24: "Timeless",
    25: "Canadian Highlander",
}

_ID_RE = re.compile(r"(\d+)")


def parse_deck_id(reference: str) -> int:
    """Accept a deck URL, an API URL, or a bare id."""
    text = str(reference).strip()
    if text.isdigit():
        return int(text)
    match = re.search(r"/decks/(\d+)", text)
    if match:
        return int(match.group(1))
    match = _ID_RE.search(text)
    if match:
        return int(match.group(1))
    raise ValueError(f"could not find an Archidekt deck id in {reference!r}")


def fetch_raw(deck_id: int, *, use_cache: bool = True) -> dict[str, Any]:
    return get_json(f"{API}/{deck_id}/", use_cache=use_cache)


def _colors_from_archidekt(values: list[str] | None) -> list[str]:
    """Archidekt spells colours out ('Green'); Scryfall uses letters ('G')."""
    if not values:
        return []
    mapped = {ARCHIDEKT_COLOR_MAP.get(v, v) for v in values}
    return [c for c in COLORS if c in mapped]


def _excluded_categories(payload: dict[str, Any]) -> set[str]:
    return {
        c["name"]
        for c in payload.get("categories") or []
        if not c.get("includedInDeck", True)
    }


def _type_line(oracle: dict[str, Any]) -> str:
    """Rebuild a Scryfall-style type line from Archidekt's split type fields."""
    front = " ".join([*(oracle.get("superTypes") or []), *(oracle.get("types") or [])])
    subtypes = oracle.get("subTypes") or []
    return f"{front} — {' '.join(subtypes)}" if subtypes else front


def _mana_production(oracle: dict[str, Any]) -> dict[str, int]:
    """Archidekt gives {"W": null, "B": 1, ...}; keep only what is produced."""
    production = oracle.get("manaProduction") or {}
    return {k: int(v) for k, v in production.items() if v}


def _oracle_text(oracle: dict[str, Any]) -> str:
    """Full rules text, including both halves of a double-faced card.

    Modal DFCs carry an empty top-level `text` and put the real rules text on
    `faces`. Reading only the top level leaves those cards textless, which
    silently breaks every downstream check that reads oracle text.
    """
    text = oracle.get("text") or ""
    faces = oracle.get("faces") or []
    if faces:
        face_text = [f.get("text", "") for f in faces if f.get("text")]
        if face_text:
            joined = "\n//\n".join(face_text)
            return f"{text}\n//\n{joined}" if text else joined
    return text


def _tags(oracle: dict[str, Any]) -> list[str]:
    """Functional tags Archidekt ships in the payload we already download.

    These are Scryfall Tagger's vocabulary, at full coverage on deck cards and
    at no extra request cost. `inheritedTags` carries tags a card picks up from
    its cycle or reprint family, which are weaker but still useful.
    """
    tags = list(oracle.get("oTags") or [])
    for tag in oracle.get("inheritedTags") or []:
        if tag not in tags:
            tags.append(tag)
    return tags


def _mana_cost(oracle: dict[str, Any]) -> str:
    """Mana cost, falling back to the faces for split and modal cards."""
    cost = oracle.get("manaCost") or ""
    if cost:
        return cost
    faces = oracle.get("faces") or []
    return " ".join(f.get("manaCost", "") for f in faces if f.get("manaCost"))


def normalise(payload: dict[str, Any], slug: str, *, enrich: bool = True) -> Deck:
    """Turn a raw Archidekt payload into a slim, enriched Deck."""
    excluded_cats = _excluded_categories(payload)

    deck = Deck(
        slug=slug,
        name=payload.get("name") or f"Deck {payload.get('id')}",
        archidekt_id=int(payload.get("id", 0)),
        deck_format=payload.get("deckFormat") or 0,
        format_name=DECK_FORMATS.get(payload.get("deckFormat") or 0, "Unknown"),
        owner=(payload.get("owner") or {}).get("username", ""),
        url=f"https://archidekt.com/decks/{payload.get('id')}",
        updated_at=payload.get("updatedAt", ""),
        archidekt_bracket=payload.get("edhBracket"),
        imported_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )

    for entry in payload.get("cards") or []:
        card = entry.get("card") or {}
        oracle = card.get("oracleCard") or {}
        name = oracle.get("name") or card.get("displayName") or ""
        if not name:
            continue

        categories = list(entry.get("categories") or [])

        # Any category flagged includedInDeck=false takes the card out of the
        # deck, even though it still carries its normal type category.
        if any(c in excluded_cats for c in categories):
            deck.excluded.append(
                {
                    "name": name,
                    "quantity": entry.get("quantity", 1),
                    "categories": categories,
                }
            )
            continue

        legalities = oracle.get("legalities") or {}
        deck.cards.append(
            CardEntry(
                name=name,
                quantity=int(entry.get("quantity", 1)),
                scryfall_id=card.get("uid", ""),  # printing id
                oracle_id=oracle.get("uid", ""),  # oracle id — a different value
                mana_cost=_mana_cost(oracle),
                mana_value=float(oracle.get("cmc") or 0),
                colors=_colors_from_archidekt(oracle.get("colors")),
                color_identity=_colors_from_archidekt(oracle.get("colorIdentity")),
                type_line=_type_line(oracle),
                oracle_text=_oracle_text(oracle),
                mana_production=_mana_production(oracle),
                keywords=oracle.get("keywords") or [],
                categories=categories,
                edhrec_rank=oracle.get("edhrecRank"),
                salt=oracle.get("salt"),
                commander_legal=legalities.get("commander", "legal") == "legal",
                is_commander="Commander" in categories,
                is_game_changer=bool(oracle.get("gameChanger")),
                is_tutor=bool(oracle.get("tutor")),
                is_extra_turns=bool(oracle.get("extraTurns")),
                is_mass_land_denial=bool(oracle.get("massLandDenial")),
                tags=_tags(oracle),
                set_code=(card.get("edition") or {}).get("editioncode", ""),
                released_at=(card.get("edition") or {}).get("editiondate", "")
                or card.get("releasedAt", ""),
                layout=oracle.get("layout") or "",
            )
        )

    if enrich:
        enrich_from_scryfall(deck)
    return deck


def enrich_from_scryfall(deck: Deck) -> None:
    """Add what Archidekt does not carry, and backfill anything it left blank.

    Archidekt's `oracleCard` already supplies type, text, cost, mana production
    and the bracket flags, so this layer is additive: prices above all, plus a
    gap-filler for cards whose Archidekt oracle data was sparse. Enrichment is
    therefore optional — a deck imported with Scryfall unreachable is still
    fully analysable, just without prices.
    """
    unresolved: list[CardEntry] = []
    for card in deck.cards:
        data = scryfall.by_oracle_id(card.oracle_id) if card.oracle_id else None
        if data is None:
            unresolved.append(card)
            continue
        _apply_scryfall(card, data)

    if unresolved:
        try:
            resolved = scryfall.lookup_many([c.name for c in unresolved])
        except Exception:
            resolved = {}
        for card in unresolved:
            data = resolved.get(card.name.split("//")[0].strip().lower())
            if data is not None:
                _apply_scryfall(card, data)

    _mark_game_changers(deck)


def _apply_scryfall(card: CardEntry, data: dict[str, Any]) -> None:
    faces = data.get("card_faces") or []

    # Scryfall is authoritative for price, rarity and legality.
    price = (data.get("prices") or {}).get("usd")
    if price:
        try:
            card.price_usd = float(price)
        except (TypeError, ValueError):
            card.price_usd = None
    card.rarity = data.get("rarity") or card.rarity
    legalities = data.get("legalities") or {}
    if "commander" in legalities:
        card.commander_legal = legalities["commander"] == "legal"

    # Everything below only fills a gap Archidekt left, so good data is never
    # overwritten with something weaker.
    if not card.type_line:
        card.type_line = data.get("type_line") or (
            faces[0].get("type_line", "") if faces else ""
        )
    if not card.mana_cost:
        card.mana_cost = data.get("mana_cost") or ""
        if not card.mana_cost and faces:
            # Split and modal cards carry the cost per face; join them so pip
            # counting sees the card's full colour demand.
            card.mana_cost = " ".join(
                f.get("mana_cost", "") for f in faces if f.get("mana_cost")
            )
    if not card.oracle_text:
        card.oracle_text = data.get("oracle_text") or "\n//\n".join(
            f.get("oracle_text", "") for f in faces
        )
    if not card.colors:
        card.colors = data.get("colors") or (faces[0].get("colors", []) if faces else [])
    if not card.color_identity:
        card.color_identity = data.get("color_identity") or []
    if not card.mana_production:
        card.mana_production = {c: 1 for c in (data.get("produced_mana") or [])}
    if not card.keywords:
        card.keywords = data.get("keywords") or []
    if card.edhrec_rank is None and data.get("edhrec_rank") is not None:
        card.edhrec_rank = data["edhrec_rank"]
    if not card.mana_value and data.get("cmc") is not None:
        card.mana_value = float(data["cmc"])


def _mark_game_changers(deck: Deck) -> None:
    """Backfill Game Changer flags only if Archidekt supplied none.

    Archidekt sets `gameChanger` on its oracle data, so the Scryfall search is
    a fallback for older payloads rather than a routine network call.
    """
    if any(c.is_game_changer for c in deck.cards):
        return
    try:
        changers = scryfall.game_changers()
    except Exception:
        return
    for card in deck.cards:
        if card.name.split("//")[0].strip().lower() in changers:
            card.is_game_changer = True


def fetch_deck(reference: str, slug: str, *, use_cache: bool = True) -> tuple[Deck, dict[str, Any]]:
    """Fetch and normalise in one step. Returns (deck, raw_payload)."""
    deck_id = parse_deck_id(reference)
    payload = fetch_raw(deck_id, use_cache=use_cache)
    return normalise(payload, slug), payload
