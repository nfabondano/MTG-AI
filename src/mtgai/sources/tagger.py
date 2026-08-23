"""Scryfall Tagger — functional tags for every card in Magic.

Archidekt already ships these tags for cards *in* a deck, so this source exists
for the two things Archidekt cannot do:

1. **The tag hierarchy.** `sacrifice-outlet` has only 12 cards tagged directly,
   but its children hold far more — `repeatable-sacrifice-outlet` 599,
   `sacrifice-outlet-artifact` 292, `free-sacrifice-outlet` 191. Without
   rolling children up into their parents you would conclude Magic contains a
   dozen sacrifice outlets.
2. **Cards outside the deck**, which is what suggestions need: "find me
   sacrifice outlets I could legally cast."

The bulk file is ~6 MB gzipped and joins on `oracle_id`, which every CardEntry
already carries.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterator

from ..config import cache_dir, scryfall_db_path
from ..http import SourceError, download, get_json

API = "https://api.scryfall.com"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS card_tags (
    oracle_id TEXT NOT NULL,
    tag       TEXT NOT NULL,
    PRIMARY KEY (oracle_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_card_tags_tag ON card_tags(tag);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(scryfall_db_path())
    conn.executescript(_SCHEMA)
    return conn


def status() -> dict[str, Any]:
    if not scryfall_db_path().exists():
        return {"present": False, "taggings": 0, "tags": 0}
    conn = _connect()
    try:
        rows = conn.execute("SELECT COUNT(*), COUNT(DISTINCT tag) FROM card_tags").fetchone()
    finally:
        conn.close()
    return {"present": rows[0] > 0, "taggings": rows[0], "tags": rows[1]}


def _ancestors(tag_id: str, parents: dict[str, list[str]], seen: set[str] | None = None) -> set[str]:
    """Every ancestor id of a tag, guarding against cycles in the graph."""
    seen = seen or set()
    out: set[str] = set()
    for parent in parents.get(tag_id, []):
        if parent in seen:
            continue
        seen.add(parent)
        out.add(parent)
        out |= _ancestors(parent, parents, seen)
    return out


def _rows(path: Path) -> Iterator[tuple[str, str]]:
    """Yield (oracle_id, tag_slug), with every ancestor tag rolled in."""
    tags: dict[str, dict[str, Any]] = {}
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            tag = json.loads(line)
            if tag.get("type") != "oracle" or not tag.get("slug"):
                continue
            tags[tag["id"]] = tag

    parents = {tid: (t.get("parent_ids") or []) for tid, t in tags.items()}
    slug_of = {tid: t["slug"] for tid, t in tags.items()}

    for tag_id, tag in tags.items():
        # A card tagged `free-sacrifice-outlet` is also a `sacrifice-outlet`.
        slugs = {slug_of[tag_id]}
        slugs |= {slug_of[a] for a in _ancestors(tag_id, parents) if a in slug_of}
        for tagging in tag.get("taggings") or []:
            oracle_id = tagging.get("oracle_id")
            if not oracle_id:
                continue
            for slug in slugs:
                yield oracle_id, slug


def refresh(*, force: bool = False) -> dict[str, Any]:
    """Download the oracle-tags bulk file and index it, hierarchy resolved."""
    catalogue = get_json(f"{API}/bulk-data", ttl=3600)
    entry = next(
        (b for b in catalogue.get("data", []) if b.get("type") == "oracle_tags"), None
    )
    if entry is None:
        raise SourceError("Scryfall bulk-data has no oracle_tags entry")

    current = status()
    if not force and current["present"]:
        return {"refreshed": False, **current}

    url = entry.get("jsonl_download_uri") or entry.get("download_uri")
    if not url:
        raise SourceError("Scryfall oracle_tags entry exposes no download URI")

    dest = cache_dir() / Path(url).name
    download(url, dest)

    conn = _connect()
    try:
        conn.execute("DELETE FROM card_tags")
        conn.executemany(
            "INSERT OR IGNORE INTO card_tags (oracle_id, tag) VALUES (?, ?)", _rows(dest)
        )
        conn.commit()
        rows = conn.execute("SELECT COUNT(*), COUNT(DISTINCT tag) FROM card_tags").fetchone()
    finally:
        conn.close()

    dest.unlink(missing_ok=True)
    return {"refreshed": True, "present": True, "taggings": rows[0], "tags": rows[1]}


def tags_for(oracle_id: str) -> set[str]:
    if not oracle_id or not scryfall_db_path().exists():
        return set()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT tag FROM card_tags WHERE oracle_id = ?", (oracle_id,)
        ).fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows}


def cards_with_tag(tag: str, *, limit: int = 500) -> set[str]:
    """Oracle ids carrying a tag, ancestors included."""
    if not scryfall_db_path().exists():
        return set()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT oracle_id FROM card_tags WHERE tag = ? LIMIT ?", (tag, limit)
        ).fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows}
