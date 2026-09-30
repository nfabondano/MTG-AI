"""Commander Spellbook's find-my-combos: the whole list in one request.

The probe method it replaced ignored a variant's generic requirements, which
is how Felisa's report came to list fifteen "two-card combos" — Ashnod's Altar
+ Cathars' Crusade needs a Persist creature the deck does not run. Spellbook's
own check, on the recorded response, finds four, all of three cards.
"""

from __future__ import annotations

import pytest

from mtgai.analysis import combos as combos_analysis
from mtgai.http import SourceError
from mtgai.sources import spellbook


def _serve(monkeypatch, pages):
    """Answer post_json with recorded pages, recording each request."""
    calls: list[tuple[str, dict]] = []
    queue = list(pages)

    def fake_post(url, body, **kwargs):
        calls.append((url, body))
        return queue.pop(0)

    monkeypatch.setattr(spellbook, "post_json", fake_post)
    return calls


class TestFindMyCombos:
    def test_felisa_has_four_three_card_combos(self, monkeypatch, felisa, spellbook_fmc):
        _serve(monkeypatch, [spellbook_fmc["felisa"]])
        result = combos_analysis.analyse(felisa)
        assert result["method"] == "find-my-combos" and not result["partial"]
        assert len(result["complete"]) == 4
        assert {c["size"] for c in result["complete"]} == {3}
        assert all("Basri's Lieutenant" in c["cards"] for c in result["complete"])

    def test_template_combos_are_not_complete(self, monkeypatch, felisa, spellbook_fmc):
        """"Needs a Persist creature" is a combo the deck cannot be credited with."""
        _serve(monkeypatch, [spellbook_fmc["felisa"]])
        result = combos_analysis.analyse(felisa)
        complete_ids = {c["id"] for c in result["complete"]}
        for combo in result["needs_template"]:
            assert combo["requires"] and combo["missing_template"]
            assert combo["id"] not in complete_ids

    def test_niv_combos_are_two_card(self, monkeypatch, niv, spellbook_fmc):
        _serve(monkeypatch, [spellbook_fmc["niv"]])
        result = combos_analysis.analyse(niv)
        assert len(result["complete"]) == 18
        assert all(c["size"] == 2 for c in result["complete"])
        pairs = {frozenset(c["cards"]) for c in result["complete"]}
        assert frozenset({"Sanguine Bond", "Exquisite Blood"}) in pairs

    def test_near_misses_name_the_missing_card(self, monkeypatch, niv, spellbook_fmc):
        _serve(monkeypatch, [spellbook_fmc["niv"]])
        result = combos_analysis.analyse(niv)
        in_deck = niv.names()
        for combo in result["near_miss"]:
            assert combo["missing"]
            assert combo["missing"].split("//")[0].strip().lower() not in in_deck

    def test_request_body_is_stable(self, monkeypatch, felisa, spellbook_fmc):
        """Sorted names, the commander apart, no basics: one cache entry per list."""
        calls = _serve(monkeypatch, [spellbook_fmc["felisa"]])
        combos_analysis.analyse(felisa)
        (url, body), = calls
        assert url == spellbook.FIND_MY_COMBOS
        assert body["commanders"] == [{"card": "Felisa, Fang of Silverquill", "quantity": 1}]
        main = [e["card"] for e in body["main"]]
        assert main == sorted(main)
        assert "Plains" not in main and "Swamp" not in main
        assert "Felisa, Fang of Silverquill" not in main

    def test_pages_are_followed(self, monkeypatch, felisa, spellbook_fmc):
        first = dict(spellbook_fmc["felisa"], next="https://example.test/page2")
        second = {"results": {"included": [], "almostIncluded": []}, "next": None}
        calls = _serve(monkeypatch, [first, second])
        combos_analysis.analyse(felisa)
        assert [url for url, _ in calls] == [spellbook.FIND_MY_COMBOS, "https://example.test/page2"]


class TestFallback:
    def test_probe_when_find_my_combos_fails(self, monkeypatch, felisa):
        def down(*a, **k):
            raise SourceError("spellbook down")

        monkeypatch.setattr(spellbook, "find_my_combos", down)
        monkeypatch.setattr(
            spellbook, "find_in_deck",
            lambda *a, **k: {"complete": [], "near_miss": [], "needs_template": []},
        )
        result = combos_analysis.analyse(felisa)
        assert result["available"] and result["partial"]
        assert result["method"] == "probe"
        assert "may be missing" in result["note"]

    def test_probe_does_not_credit_template_combos(self, monkeypatch):
        with_template = spellbook.Combo(
            id="t", cards=["Ashnod's Altar", "Cathars' Crusade"], requires=["Persist Creature"]
        )
        monkeypatch.setattr(spellbook, "combos_using", lambda name, **k: [with_template])
        found = spellbook.find_in_deck({"ashnod's altar", "cathars' crusade"}, ["Ashnod's Altar"])
        assert found["complete"] == []
        assert [c.id for c in found["needs_template"]] == ["t"]


class TestParsing:
    @pytest.mark.parametrize("raw,expected", [("C", ""), ("", ""), (None, ""), ("WB", "WB")])
    def test_identity(self, raw, expected):
        assert spellbook._normalise_identity(raw) == expected

    def test_size_counts_quantities_and_templates(self):
        combo = spellbook._parse(
            {
                "id": "x",
                "uses": [{"card": {"name": "A"}, "quantity": 1}, {"card": {"name": "B"}}],
                "requires": [{"template": {"name": "Persist Creature"}, "quantity": 1}],
                "produces": [],
                "identity": "W",
                "bracketTag": "E",
            }
        )
        assert combo.size == 3
        assert combo.requires == ["Persist Creature"]
        assert combo.bracket_tag == "E"
