"""Trim recorded API payloads down to what the tool reads, for test fixtures.

A real Archidekt payload runs to ~400 KB; the normaliser reads a small subset of
it. Trimming keeps the fixtures reviewable while still being *real* data — the
oTags, flags and oracle text Archidekt actually ships, which is exactly what
the false-advice regressions depend on.

Usage:
    uv run python scripts/trim_fixture.py archidekt decks/<slug>/source.json tests/fixtures/<name>.json
    uv run python scripts/trim_fixture.py spellbook <find-my-combos response.json> tests/fixtures/<name>.json

The Archidekt mode checks that the trimmed payload normalises to the same deck
as the original before writing it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Everything `sources.archidekt.normalise` reads, and nothing else.
_TOP = ("id", "name", "deckFormat", "edhBracket", "updatedAt", "description", "deckTags")
_ORACLE = (
    "name", "uid", "cmc", "colorIdentity", "colors", "manaCost", "text", "types",
    "superTypes", "subTypes", "manaProduction", "keywords", "edhrecRank",
    "gameChanger", "tutor", "extraTurns", "massLandDenial", "oTags",
    "inheritedTags", "layout",
)
_FACE = ("name", "manaCost", "text")

# find-my-combos: the fields `sources.spellbook` parses, and how many near
# misses to keep (all of `included` is kept — the bracket counts it).
_NEAR_MISSES_KEPT = 12


def trim_archidekt(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {k: payload.get(k) for k in _TOP}
    out["owner"] = {"username": (payload.get("owner") or {}).get("username", "")}
    out["categories"] = [
        {"name": c.get("name"), "includedInDeck": c.get("includedInDeck", True)}
        for c in payload.get("categories") or []
    ]
    cards = []
    for entry in payload.get("cards") or []:
        card = entry.get("card") or {}
        oracle = card.get("oracleCard") or {}
        trimmed_oracle = {k: oracle.get(k) for k in _ORACLE if k in oracle}
        if oracle.get("faces"):
            trimmed_oracle["faces"] = [
                {k: f.get(k) for k in _FACE if k in f} for f in oracle["faces"]
            ]
        # Only truthiness matters to the normaliser.
        trimmed_oracle["potentialCombos"] = bool(
            oracle.get("potentialCombos") or oracle.get("twoCardComboSingelton")
        )
        trimmed_oracle["legalities"] = {
            "commander": (oracle.get("legalities") or {}).get("commander", "legal")
        }
        edition = card.get("edition") or {}
        cards.append(
            {
                "quantity": entry.get("quantity", 1),
                "categories": list(entry.get("categories") or []),
                "card": {
                    "uid": card.get("uid", ""),
                    "edition": {
                        "editioncode": edition.get("editioncode", ""),
                        "editiondate": edition.get("editiondate", ""),
                    },
                    "releasedAt": card.get("releasedAt", ""),
                    "oracleCard": trimmed_oracle,
                },
            }
        )
    out["cards"] = cards
    return out


def _trim_variant(variant: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": variant.get("id"),
        "uses": [
            {
                "card": {"name": (u.get("card") or {}).get("name")},
                "quantity": u.get("quantity", 1),
                "mustBeCommander": u.get("mustBeCommander", False),
            }
            for u in variant.get("uses") or []
        ],
        "requires": [
            {
                "template": {"name": (r.get("template") or {}).get("name")},
                "quantity": r.get("quantity", 1),
            }
            for r in variant.get("requires") or []
        ],
        "produces": [
            {"feature": {"name": (p.get("feature") or {}).get("name")}}
            for p in variant.get("produces") or []
        ],
        "identity": variant.get("identity"),
        "bracketTag": variant.get("bracketTag"),
        "popularity": variant.get("popularity"),
    }


def trim_spellbook(response: dict[str, Any]) -> dict[str, Any]:
    results = response.get("results") or {}
    almost = results.get("almostIncluded") or []
    # Keep the most popular near misses, plus a few that need a template piece
    # ("Persist Creature") so the parser's template path stays covered.
    kept = almost[:_NEAR_MISSES_KEPT]
    kept += [v for v in almost[_NEAR_MISSES_KEPT:] if v.get("requires")][:3]
    return {
        "count": response.get("count"),
        "next": None,
        "previous": None,
        "results": {
            "identity": results.get("identity"),
            "included": [_trim_variant(v) for v in results.get("included") or []],
            "almostIncluded": [_trim_variant(v) for v in kept],
        },
    }


def _same_deck(original: dict[str, Any], trimmed: dict[str, Any]) -> None:
    """Fail loudly if trimming changed what the tool sees."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from mtgai.sources import archidekt

    def comparable(payload: dict[str, Any]) -> dict[str, Any]:
        data = archidekt.normalise(payload, "check", enrich=False).to_dict()
        data.pop("imported_at", None)
        for card in data["cards"]:
            card.pop("salt", None)
        return data

    if comparable(original) != comparable(trimmed):
        raise SystemExit("trimmed payload normalises differently — refusing to write it")


def main(argv: list[str]) -> None:
    if len(argv) != 4 or argv[1] not in {"archidekt", "spellbook"}:
        raise SystemExit(__doc__)
    kind, source, dest = argv[1], Path(argv[2]), Path(argv[3])
    original = json.loads(source.read_text())
    if kind == "archidekt":
        trimmed = trim_archidekt(original)
        _same_deck(original, trimmed)
    else:
        trimmed = trim_spellbook(original)
    dest.write_text(json.dumps(trimmed, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"{dest}: {dest.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    main(sys.argv)
