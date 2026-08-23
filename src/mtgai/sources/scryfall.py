"""Scryfall access: a local bulk cache backed by the live API.

Scryfall's bulk endpoint now serves gzipped JSONL under `jsonl_download_uri`
(there is no `download_uri` key any more). The Oracle Cards file is ~24 MB
compressed and holds one entry per distinct card, which is exactly the grain
deck analysis wants.

The cache lives outside the repo because it is large and fully regenerable.
Anything it misses falls back to the live API, batched 75 at a time through
POST /cards/collection.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from ..config import cache_dir, scryfall_db_path
from ..http import SourceError, download, get_json, post_json

API = "https://api.scryfall.com"
COLLECTION_BATCH = 75  # Scryfall's documented maximum per request

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    oracle_id  TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    name_lower TEXT NOT NULL,
    data       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cards_name_lower ON cards(name_lower);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(scryfall_db_path())
    conn.executescript(_SCHEMA)
    return conn


def _normalise(name: str) -> str:
    """Match on the front face so `Ale // Ile` finds `Ale`."""
    return name.split("//")[0].strip().lower()


def cache_status() -> dict[str, Any]:
    path = scryfall_db_path()
    if not path.exists():
        return {"present": False, "cards": 0, "updated_at": None}
    conn = _connect()
    try:
        count = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        row = conn.execute("SELECT value FROM meta WHERE key = 'updated_at'").fetchone()
    finally:
        conn.close()
    return {"present": count > 0, "cards": count, "updated_at": row[0] if row else None}


def refresh_cache(*, force: bool = False) -> dict[str, Any]:
    """Download the Oracle Cards bulk file into SQLite."""
    catalogue = get_json(f"{API}/bulk-data", ttl=3600)
    entry = next(
        (b for b in catalogue.get("data", []) if b.get("type") == "oracle_cards"), None
    )
    if entry is None:
        raise SourceError("Scryfall bulk-data has no oracle_cards entry")

    remote_updated = entry.get("updated_at", "")
    status = cache_status()
    if not force and status["present"] and status["updated_at"] == remote_updated:
        return {"refreshed": False, **status}

    url = entry.get("jsonl_download_uri") or entry.get("download_uri")
    if not url:
        raise SourceError("Scryfall bulk entry exposes no download URI")

    dest = cache_dir() / Path(url).name
    download(url, dest)

    conn = _connect()
    try:
        conn.execute("DELETE FROM cards")
        conn.executemany(
            "INSERT OR REPLACE INTO cards (oracle_id, name, name_lower, data)"
            " VALUES (?, ?, ?, ?)",
            _iter_rows(dest),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('updated_at', ?)",
            (remote_updated,),
        )
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    finally:
        conn.close()

    dest.unlink(missing_ok=True)
    return {"refreshed": True, "present": True, "cards": count, "updated_at": remote_updated}


def _iter_rows(path: Path):
    """Yield (oracle_id, name, name_lower, json) from a bulk file.

    Handles both the current gzipped JSONL format and a plain JSON array, so a
    future format change on Scryfall's side degrades rather than breaks.
    """
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        first = fh.read(1)
        fh.seek(0)
        if first == "[":
            cards: Iterable[dict[str, Any]] = json.load(fh)
        else:
            cards = (json.loads(line) for line in fh if line.strip())
        for card in cards:
            oracle_id = card.get("oracle_id")
            name = card.get("name")
            if not oracle_id or not name:
                continue
            yield oracle_id, name, _normalise(name), json.dumps(card)


def by_oracle_id(oracle_id: str) -> dict[str, Any] | None:
    if not oracle_id or not scryfall_db_path().exists():
        return None
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT data FROM cards WHERE oracle_id = ?", (oracle_id,)
        ).fetchone()
    finally:
        conn.close()
    return json.loads(row[0]) if row else None


def by_name(name: str) -> dict[str, Any] | None:
    if not name or not scryfall_db_path().exists():
        return None
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT data FROM cards WHERE name_lower = ?", (_normalise(name),)
        ).fetchone()
    finally:
        conn.close()
    return json.loads(row[0]) if row else None


def lookup(*, oracle_id: str = "", name: str = "") -> dict[str, Any] | None:
    """Local cache first, then the live API."""
    card = by_oracle_id(oracle_id) if oracle_id else None
    if card is None and name:
        card = by_name(name)
    if card is not None:
        return card
    if name:
        try:
            return get_json(f"{API}/cards/named", params={"exact": name})
        except SourceError:
            return None
    return None


def lookup_many(names: list[str]) -> dict[str, dict[str, Any]]:
    """Resolve many cards at once, keyed by lowercase front-face name.

    Everything the local cache holds is answered for free; the remainder goes
    to the live API in batches of 75.
    """
    resolved: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for name in names:
        card = by_name(name)
        if card is not None:
            resolved[_normalise(name)] = card
        else:
            missing.append(name)

    for start in range(0, len(missing), COLLECTION_BATCH):
        batch = missing[start : start + COLLECTION_BATCH]
        try:
            payload = post_json(
                f"{API}/cards/collection",
                {"identifiers": [{"name": n} for n in batch]},
            )
        except SourceError:
            continue
        for card in payload.get("data", []):
            resolved[_normalise(card["name"])] = card
    return resolved


def search(query: str, *, max_pages: int = 10) -> list[dict[str, Any]]:
    """Run a Scryfall search, following pagination."""
    results: list[dict[str, Any]] = []
    url: str | None = f"{API}/cards/search"
    params: dict[str, Any] | None = {"q": query, "unique": "cards"}
    for _ in range(max_pages):
        if url is None:
            break
        try:
            payload = get_json(url, params=params)
        except SourceError:
            break
        results.extend(payload.get("data", []))
        url = payload.get("next_page")
        params = None  # next_page carries its own query string
        if not payload.get("has_more"):
            break
    return results


def game_changers() -> set[str]:
    """The Game Changer list, which drives Commander bracket estimation."""
    return {_normalise(c["name"]) for c in search("is:gamechanger")}
