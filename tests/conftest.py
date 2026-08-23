"""Shared test fixtures.

Every test runs offline: fixtures are recorded payloads, and the cache and deck
directories are redirected into tmp_path so a test run never touches the real
repository or the user's cache.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def synthetic_payload() -> dict:
    """Hand-built payload covering the parsing traps explicitly."""
    return load_fixture("archidekt_synthetic.json")


@pytest.fixture
def maybeboard_payload() -> dict:
    """A real Archidekt deck, trimmed, that has a populated maybeboard."""
    return load_fixture("archidekt_maybeboard.json")


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    """Keep tests away from the real decks/ folder and ~/.cache/mtgai."""
    monkeypatch.setenv("MTGAI_ROOT", str(tmp_path / "repo"))
    monkeypatch.setenv("MTGAI_CACHE_DIR", str(tmp_path / "cache"))
    (tmp_path / "repo").mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture(autouse=True)
def no_network(monkeypatch, request):
    """Fail loudly if a test reaches for the network.

    Opt out with @pytest.mark.allow_network for tests that stub HTTP themselves.
    """
    if request.node.get_closest_marker("allow_network"):
        return

    def _blocked(*args, **kwargs):
        raise AssertionError("test attempted a network request")

    import httpx

    monkeypatch.setattr(httpx.Client, "request", _blocked)
    monkeypatch.setattr(httpx.Client, "stream", _blocked)
