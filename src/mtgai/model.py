"""Normalised deck representation.

The Archidekt payload for a 100-card deck is ~300 KB, which is far too large to
read into a session. Everything downstream works from these slim structures
instead, which serialise to roughly 30-40 KB and are self-contained: once a
deck is imported, analysing it or answering questions about it needs no network
access at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

COLORS = ("W", "U", "B", "R", "G")
COLOR_NAMES = {
    "W": "White",
    "U": "Blue",
    "B": "Black",
    "R": "Red",
    "G": "Green",
}

# Archidekt spells colours out; Scryfall uses single letters.
ARCHIDEKT_COLOR_MAP = {
    "White": "W",
    "Blue": "U",
    "Black": "B",
    "Red": "R",
    "Green": "G",
}

_SYMBOL_RE = re.compile(r"\{([^}]+)\}")

BASIC_LANDS = {
    "Plains",
    "Island",
    "Swamp",
    "Mountain",
    "Forest",
    "Wastes",
    "Snow-Covered Plains",
    "Snow-Covered Island",
    "Snow-Covered Swamp",
    "Snow-Covered Mountain",
    "Snow-Covered Forest",
}


def color_pips(mana_cost: str) -> dict[str, int]:
    """Count coloured mana symbols in a mana cost.

    Hybrid ({W/U}) counts toward both halves and phyrexian ({W/P}) toward its
    colour, because both still represent a demand for that colour when you
    cannot pay the alternative.
    """
    pips = {c: 0 for c in COLORS}
    for symbol in _SYMBOL_RE.findall(mana_cost or ""):
        for color in COLORS:
            if color in symbol.upper().split("/"):
                pips[color] += 1
    return pips


@dataclass
class CardEntry:
    """One card in a deck, enriched with the Scryfall data analysis needs."""

    name: str
    quantity: int = 1
    scryfall_id: str = ""  # printing id (Archidekt card.uid)
    oracle_id: str = ""  # oracle id (Archidekt card.oracleCard.uid)
    mana_cost: str = ""
    mana_value: float = 0.0
    colors: list[str] = field(default_factory=list)
    color_identity: list[str] = field(default_factory=list)
    type_line: str = ""
    oracle_text: str = ""
    # Colour -> amount produced. Archidekt supplies amounts, which Scryfall's
    # flat `produced_mana` list does not, so this drives mana-source counting.
    mana_production: dict[str, int] = field(default_factory=dict)
    keywords: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    edhrec_rank: int | None = None
    salt: float | None = None
    price_usd: float | None = None
    commander_legal: bool = True
    is_commander: bool = False
    # Flags Archidekt precomputes that map onto the Commander bracket criteria.
    is_game_changer: bool = False
    is_tutor: bool = False
    is_extra_turns: bool = False
    is_mass_land_denial: bool = False
    roles: list[str] = field(default_factory=list)
    set_code: str = ""
    rarity: str = ""

    @property
    def is_land(self) -> bool:
        return "Land" in self.type_line

    @property
    def is_basic_land(self) -> bool:
        return self.name in BASIC_LANDS or "Basic" in self.type_line

    @property
    def primary_type(self) -> str:
        """The face type used for grouping, ignoring supertypes."""
        front = self.type_line.split("//")[0]
        face = front.split("—")[0].strip()
        for candidate in (
            "Land",
            "Creature",
            "Artifact",
            "Enchantment",
            "Instant",
            "Sorcery",
            "Planeswalker",
            "Battle",
        ):
            if candidate in face:
                return candidate
        return "Other"

    def pips(self) -> dict[str, int]:
        return color_pips(self.mana_cost)

    def produces(self) -> set[str]:
        """Coloured mana this card can produce (colourless excluded)."""
        return {
            color
            for color, amount in (self.mana_production or {}).items()
            if color in COLORS and amount
        }

    def to_dict(self) -> dict[str, Any]:
        data = {
            "name": self.name,
            "quantity": self.quantity,
            "scryfall_id": self.scryfall_id,
            "oracle_id": self.oracle_id,
            "mana_cost": self.mana_cost,
            "mana_value": self.mana_value,
            "colors": self.colors,
            "color_identity": self.color_identity,
            "type_line": self.type_line,
            "oracle_text": self.oracle_text,
            "mana_production": self.mana_production,
            "keywords": self.keywords,
            "categories": self.categories,
            "edhrec_rank": self.edhrec_rank,
            "salt": self.salt,
            "price_usd": self.price_usd,
            "commander_legal": self.commander_legal,
            "is_commander": self.is_commander,
            "is_game_changer": self.is_game_changer,
            "is_tutor": self.is_tutor,
            "is_extra_turns": self.is_extra_turns,
            "is_mass_land_denial": self.is_mass_land_denial,
            "roles": self.roles,
            "set_code": self.set_code,
            "rarity": self.rarity,
        }
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CardEntry:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class Deck:
    """A normalised deck plus the Archidekt metadata worth keeping."""

    slug: str
    name: str
    archidekt_id: int
    deck_format: int = 3  # 3 == Commander
    format_name: str = "Commander"
    owner: str = ""
    url: str = ""
    updated_at: str = ""
    archidekt_bracket: int | None = None
    imported_at: str = ""
    cards: list[CardEntry] = field(default_factory=list)
    excluded: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_commander(self) -> bool:
        return self.deck_format == 3

    @property
    def commanders(self) -> list[CardEntry]:
        return [c for c in self.cards if c.is_commander]

    @property
    def maindeck(self) -> list[CardEntry]:
        """Every card that counts toward the 100, commanders included."""
        return list(self.cards)

    @property
    def nonland(self) -> list[CardEntry]:
        return [c for c in self.cards if not c.is_land]

    @property
    def lands(self) -> list[CardEntry]:
        return [c for c in self.cards if c.is_land]

    @property
    def total_cards(self) -> int:
        return sum(c.quantity for c in self.cards)

    def color_identity(self) -> list[str]:
        """The deck's legal colour identity — its commanders' combined identity.

        Falls back to the union of all cards for decks with no commander set,
        so the report still has something sensible to show.
        """
        commanders = self.commanders
        source = commanders if commanders else self.cards
        identity = {c for card in source for c in card.color_identity}
        return [c for c in COLORS if c in identity]

    def find(self, name: str) -> CardEntry | None:
        target = name.strip().lower()
        for card in self.cards:
            if card.name.lower() == target:
                return card
        # Match the front face of a double-faced card ("Ale // Ile" -> "Ale").
        for card in self.cards:
            if card.name.split("//")[0].strip().lower() == target:
                return card
        return None

    def names(self) -> set[str]:
        names: set[str] = set()
        for card in self.cards:
            names.add(card.name.lower())
            names.add(card.name.split("//")[0].strip().lower())
        return names

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "archidekt_id": self.archidekt_id,
            "deck_format": self.deck_format,
            "format_name": self.format_name,
            "owner": self.owner,
            "url": self.url,
            "updated_at": self.updated_at,
            "archidekt_bracket": self.archidekt_bracket,
            "imported_at": self.imported_at,
            "color_identity": self.color_identity(),
            "total_cards": self.total_cards,
            "commanders": [c.name for c in self.commanders],
            "cards": [c.to_dict() for c in self.cards],
            "excluded": self.excluded,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Deck:
        return cls(
            slug=data["slug"],
            name=data["name"],
            archidekt_id=data["archidekt_id"],
            deck_format=data.get("deck_format", 3),
            format_name=data.get("format_name", "Commander"),
            owner=data.get("owner", ""),
            url=data.get("url", ""),
            updated_at=data.get("updated_at", ""),
            archidekt_bracket=data.get("archidekt_bracket"),
            imported_at=data.get("imported_at", ""),
            cards=[CardEntry.from_dict(c) for c in data.get("cards", [])],
            excluded=data.get("excluded", []),
        )
