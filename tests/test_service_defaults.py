"""Service-level defaults that used to be lost between commands."""

from __future__ import annotations

from conftest import REAL_DECKS, load_fixture
from mtgai import deckfolder, service
from mtgai.sources import archidekt


def _serve(monkeypatch, key: str) -> None:
    payload = load_fixture(REAL_DECKS[key][0])
    monkeypatch.setattr(archidekt, "fetch_raw", lambda deck_id, use_cache=True: payload)


def test_offline_import_stays_offline(monkeypatch):
    """`analyze --offline` rendered suggestions without the flag, and the
    suggestion builder then went to Scryfall for candidate cards."""
    _serve(monkeypatch, "felisa")
    added = service.add_deck("6313712", offline=True)  # the no-network guard would fail this
    assert added["deck"]["total_cards"] == 106


def test_suggestions_keep_the_declared_bracket(monkeypatch):
    """Re-analysing used to rewrite suggestions.md without the bracket cap."""
    _serve(monkeypatch, "equipments")
    added = service.add_deck("14435331", offline=True)
    text = deckfolder.folder_for(added["slug"]).suggestions_path.read_text()
    assert "Keeping the deck at bracket 2 or below." in text
