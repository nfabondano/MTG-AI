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


# The three decks whose reports kept giving advice that had to be ignored.
# Real payloads, trimmed by scripts/trim_fixture.py to the fields the
# normaliser reads — the oTags and flags are exactly what Archidekt ships.
REAL_DECKS = {
    "niv": ("archidekt_niv_26941717.json", "niv-mizzet-fixture"),
    "felisa": ("archidekt_felisa_6313712.json", "felisa-fixture"),
    "equipments": ("archidekt_equipments_14435331.json", "equipments-fixture"),
}


def real_deck(key: str):
    """A freshly normalised copy of one of the real regression decks."""
    from mtgai.sources import archidekt

    fixture, slug = REAL_DECKS[key]
    return archidekt.normalise(load_fixture(fixture), slug, enrich=False)


@pytest.fixture
def niv():
    """Niv-Mizzet, Ghost Counsel — lifegain into cards, drain combos."""
    return real_deck("niv")


@pytest.fixture
def felisa():
    """Felisa, Fang of Silverquill — counters and aristocrats, 106 cards."""
    return real_deck("felisa")


@pytest.fixture
def equipments():
    """Cloud, Ex-SOLDIER — equipment, 103 cards, declared bracket 2."""
    return real_deck("equipments")


@pytest.fixture
def spellbook_fmc():
    """Recorded Commander Spellbook find-my-combos responses, by deck key."""
    return {
        "felisa": load_fixture("spellbook_fmc_felisa.json"),
        "niv": load_fixture("spellbook_fmc_niv.json"),
    }


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
