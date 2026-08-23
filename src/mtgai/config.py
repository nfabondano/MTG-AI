"""Paths and shared constants."""

from __future__ import annotations

import os
from pathlib import Path

USER_AGENT = "MTG-AI/0.1 (+https://github.com/nfabondano/MTG-AI)"

# Scryfall asks for a descriptive User-Agent and 50-100ms between requests.
# We use 100ms, and throttle the unofficial APIs at least as politely.
RATE_LIMITS = {
    "api.scryfall.com": 0.10,
    "data.scryfall.io": 0.0,  # single bulk download, no throttle needed
    "archidekt.com": 0.20,
    "json.edhrec.com": 0.20,
    "backend.commanderspellbook.com": 0.20,
}
DEFAULT_RATE_LIMIT = 0.25

# Response cache lifetimes. Card data is stable; meta data drifts daily.
CACHE_TTL = {
    "api.scryfall.com": 7 * 24 * 3600,
    "archidekt.com": 24 * 3600,
    "json.edhrec.com": 24 * 3600,
    "backend.commanderspellbook.com": 24 * 3600,
}
DEFAULT_CACHE_TTL = 24 * 3600


def project_root() -> Path:
    """Repository root — the directory holding `decks/`.

    Honours MTGAI_ROOT so the CLI works from any cwd (the MCP server and
    slash commands both rely on this).
    """
    env = os.environ.get("MTGAI_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[2]


def decks_dir() -> Path:
    return project_root() / "decks"


def cache_dir() -> Path:
    """Regenerable caches live outside the repo so they are never committed."""
    base = os.environ.get("MTGAI_CACHE_DIR")
    path = Path(base).expanduser() if base else Path.home() / ".cache" / "mtgai"
    path.mkdir(parents=True, exist_ok=True)
    return path


def http_cache_dir() -> Path:
    path = cache_dir() / "http"
    path.mkdir(parents=True, exist_ok=True)
    return path


def scryfall_db_path() -> Path:
    return cache_dir() / "scryfall.sqlite"
