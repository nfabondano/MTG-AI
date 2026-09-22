"""EDHREC themes, similar commanders, and honest comparison populations.

The Uugguu report once said "compared against ~2,065 decks with this
commander" while judging a clone-heavy build against the all-builds average,
headlined missing lands as staples, and had nothing at all to say for a
commander with no page. The commander page always carried the fix: build
variants in `panels.taglinks`, `similar` commanders, and per-variant pages.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mtgai.analysis import edhrec_delta
from mtgai.http import Forbidden, NotFound
from mtgai.intent import DeckIntent
from mtgai.model import Deck
from mtgai.sources import archidekt, edhrec

FIXTURES = Path(__file__).parent / "fixtures"

COMMANDER_URL = "https://json.edhrec.com/pages/commanders/uugguu-the-omniplasm.json"
CLONES_URL = "https://json.edhrec.com/pages/commanders/uugguu-the-omniplasm/clones.json"
OOZES_TAG_URL = "https://json.edhrec.com/pages/tags/oozes.json"


@pytest.fixture
def uugguu() -> Deck:
    payload = json.loads((FIXTURES / "archidekt_uugguu.json").read_text())
    return archidekt.normalise(payload, "uugguu-fixture", enrich=False)


@pytest.fixture
def commander_payload() -> dict:
    return json.loads((FIXTURES / "edhrec_uugguu_commander.json").read_text())


@pytest.fixture
def clones_payload() -> dict:
    return json.loads((FIXTURES / "edhrec_uugguu_clones.json").read_text())


def _route(monkeypatch, routes: dict[str, dict]):
    def fake_get_json(url: str, **kwargs):
        if url in routes:
            return routes[url]
        raise NotFound(f"not found: {url}")

    monkeypatch.setattr(edhrec, "get_json", fake_get_json)


class TestCommanderPageParsing:
    def test_themes_similar_and_deck_count_are_parsed(self, monkeypatch, commander_payload):
        _route(monkeypatch, {COMMANDER_URL: commander_payload})
        data = edhrec.commander("Uugguu, the Omniplasm")
        assert data.found
        assert data.num_decks == 2589
        names = {t["name"] for t in data.themes}
        assert {"Tokens", "Clones", "Oozes"} <= names
        assert "The Mimeoplasm" in data.similar

    def test_absent_panels_degrade_to_empty(self, monkeypatch):
        _route(monkeypatch, {COMMANDER_URL: {"container": {"json_dict": {"cardlists": []}}}})
        data = edhrec.commander("Uugguu, the Omniplasm")
        assert data.found
        assert data.themes == [] and data.similar == [] and data.num_decks == 0


class TestBuildThemeDetection:
    def test_the_clone_heavy_build_is_detected_as_clones(
        self, monkeypatch, uugguu, commander_payload
    ):
        _route(monkeypatch, {COMMANDER_URL: commander_payload})
        data = edhrec.commander("Uugguu, the Omniplasm")
        theme = edhrec_delta.detect_build_theme(uugguu, data)
        assert theme is not None and theme["slug"] == "clones"

    def test_declared_intent_outranks_cluster_evidence(
        self, monkeypatch, uugguu, commander_payload
    ):
        _route(monkeypatch, {COMMANDER_URL: commander_payload})
        data = edhrec.commander("Uugguu, the Omniplasm")
        declared = DeckIntent(archetype="Ooze typal aristocrats", tribe="Ooze")
        theme = edhrec_delta.detect_build_theme(uugguu, data, declared)
        assert theme is not None and theme["slug"] == "oozes"

    def test_tiny_themes_are_not_populations(self, uugguu):
        data = edhrec.CommanderData(slug="s", found=True)
        data.themes = [{"name": "Clones", "slug": "clones", "count": 5}]
        assert edhrec_delta.detect_build_theme(uugguu, data) is None


class TestDeltaBasis:
    def test_theme_page_becomes_the_comparison_population(
        self, monkeypatch, uugguu, commander_payload, clones_payload
    ):
        _route(monkeypatch, {COMMANDER_URL: commander_payload, CLONES_URL: clones_payload})
        result = edhrec_delta.analyse(uugguu)
        assert result["available"]
        assert result["basis"] == "theme"
        assert "115 Clones builds" in result["basis_label"]
        assert result["sample_size"] == 115
        assert result["small_sample"] is False
        # The full page's variant list and similar commanders still ride along.
        assert result["themes"] and result["similar"]

    def test_a_gated_theme_page_falls_back_to_all_builds(
        self, monkeypatch, uugguu, commander_payload
    ):
        def fake_get_json(url: str, **kwargs):
            if url == COMMANDER_URL:
                return commander_payload
            raise Forbidden(f"gated: {url}")

        monkeypatch.setattr(edhrec, "get_json", fake_get_json)
        result = edhrec_delta.analyse(uugguu)
        assert result["available"]
        assert result["basis"] == "all-builds"
        assert result["sample_size"] == 2589

    def test_lands_never_headline_missing_staples(
        self, monkeypatch, uugguu, commander_payload
    ):
        def fake_get_json(url: str, **kwargs):
            if url == COMMANDER_URL:
                return commander_payload
            raise Forbidden(f"gated: {url}")

        monkeypatch.setattr(edhrec, "get_json", fake_get_json)
        result = edhrec_delta.analyse(uugguu)
        assert all(
            e["list"] not in edhrec_delta.MANA_LISTS for e in result["missing_staples"]
        )
        assert all(
            e["list"] in edhrec_delta.MANA_LISTS
            for e in result["missing_mana_staples"]
        )


class TestPagelessCommander:
    def test_falls_back_to_the_tribe_tag_page_with_an_honest_label(
        self, monkeypatch, uugguu, commander_payload
    ):
        tag_payload = json.loads(json.dumps(commander_payload))
        tag_payload["similar"] = []
        _route(monkeypatch, {OOZES_TAG_URL: tag_payload})
        result = edhrec_delta.analyse(uugguu)
        assert result["available"]
        assert result["basis"] == "tribe-tag"
        assert "no EDHREC page for this commander yet" in result["basis_label"]
        assert "weaker signal" in result["basis_label"]

    def test_no_page_and_no_tribe_page_is_honestly_unavailable(
        self, monkeypatch, uugguu
    ):
        _route(monkeypatch, {})
        result = edhrec_delta.analyse(uugguu)
        assert result["available"] is False
        assert result["missing_staples"] == []
