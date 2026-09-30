"""EDHREC slugs, graceful degradation, and combo filtering."""

from __future__ import annotations

import pytest

from mtgai.analysis import combos as combos_analysis
from mtgai.http import Forbidden, NotFound, SourceError
from mtgai.model import CardEntry, Deck
from mtgai.sources import edhrec, spellbook


class TestSlugify:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("Atraxa, Praetor's Voice", "atraxa-praetors-voice"),
            ("Edgar Markov", "edgar-markov"),
            ("Kenrith, the Returned King", "kenrith-the-returned-king"),
            ("Sol Ring", "sol-ring"),
            # Apostrophes vanish rather than becoming separators.
            ("Gonti, Lord of Luxury", "gonti-lord-of-luxury"),
            ("Jodah, the Unifier", "jodah-the-unifier"),
            # Accents fold to ASCII.
            ("Clavileño, First of the Blessed", "clavileno-first-of-the-blessed"),
            # Only the front face is used.
            ("Bloodline Keeper // Lord of Lineage", "bloodline-keeper"),
        ],
    )
    def test_slugs(self, name, expected):
        assert edhrec.slugify(name) == expected

    def test_curly_apostrophe_matches_straight(self):
        assert edhrec.slugify("Atraxa, Praetor’s Voice") == "atraxa-praetors-voice"


class TestGracefulDegradation:
    """A gated or missing EDHREC page must never fail the whole analysis."""

    def test_forbidden_is_reported_not_raised(self, monkeypatch):
        def boom(*a, **k):
            raise Forbidden("gated", status=403, url="x")

        monkeypatch.setattr(edhrec, "get_json", boom)
        data = edhrec.commander("whoever")
        assert data.found is False
        assert "403" in data.error or "gated" in data.error

    def test_missing_page_is_reported_not_raised(self, monkeypatch):
        def boom(*a, **k):
            raise NotFound("nope", status=404, url="x")

        monkeypatch.setattr(edhrec, "get_json", boom)
        assert edhrec.commander("nobody").found is False

    def test_network_failure_is_reported_not_raised(self, monkeypatch):
        def boom(*a, **k):
            raise SourceError("network down")

        monkeypatch.setattr(edhrec, "get_json", boom)
        assert edhrec.commander("anyone").found is False

    def test_deck_with_no_commander(self):
        assert edhrec.commander_for([]).found is False


class TestRecommendationParsing:
    def test_inclusion_is_the_deck_ratio(self):
        rec = edhrec.Recommendation(name="X", num_decks=50, potential_decks=200)
        assert rec.inclusion == 0.25

    def test_zero_potential_decks_does_not_divide_by_zero(self):
        assert edhrec.Recommendation(name="X", num_decks=5, potential_decks=0).inclusion == 0.0

    def test_by_name_keeps_the_highest_synergy_entry(self):
        data = edhrec.CommanderData(slug="s", found=True)
        data.recommendations = [
            edhrec.Recommendation(name="Sol Ring", synergy=0.1, list_name="Top"),
            edhrec.Recommendation(name="Sol Ring", synergy=0.4, list_name="Synergy"),
        ]
        assert data.by_name()["sol ring"].synergy == 0.4


class TestComboIdentityFilter:
    """Suggesting a card outside the deck's colours is the worst failure mode.

    Plenty of catalogued combos pair a colourless staple the deck already runs,
    like Sol Ring, with a piece the deck could never cast.
    """

    def _deck(self):
        return Deck(
            slug="t",
            name="T",
            archidekt_id=1,
            cards=[
                CardEntry(
                    name="Mardu Boss",
                    type_line="Legendary Creature",
                    color_identity=["W", "B", "R"],
                    is_commander=True,
                ),
                CardEntry(name="Sol Ring", type_line="Artifact"),
            ],
        )

    @staticmethod
    def _serve(monkeypatch, found):
        monkeypatch.setattr(spellbook, "find_my_combos", lambda *a, **k: found)

    def test_off_identity_combos_are_dropped(self, monkeypatch):
        blue = spellbook.Combo(id="1", cards=["Sol Ring", "Hullbreaker Horror"], identity="U")
        mardu = spellbook.Combo(id="2", cards=["Sol Ring", "Ad Nauseam"], identity="B")
        blue.missing, mardu.missing = "Hullbreaker Horror", "Ad Nauseam"
        self._serve(monkeypatch, {"complete": [], "near_miss": [blue, mardu]})
        result = combos_analysis.analyse(self._deck())
        names = {c["id"] for c in result["near_miss"]}
        assert names == {"2"}, "a blue combo must not be suggested to a Mardu deck"

    @pytest.mark.parametrize("identity", ["", "C"])
    def test_colourless_combos_are_kept(self, monkeypatch, identity):
        """Spellbook writes colourless as "C"; that used to fail every deck."""
        colourless = spellbook.Combo(
            id="3", cards=["Sol Ring", "Basalt Monolith"], identity=identity
        )
        self._serve(monkeypatch, {"complete": [colourless], "near_miss": []})
        result = combos_analysis.analyse(self._deck())
        assert len(result["complete"]) == 1

    def test_source_failure_degrades_quietly(self, monkeypatch):
        def boom(*a, **k):
            raise SourceError("spellbook down")

        monkeypatch.setattr(spellbook, "find_my_combos", boom)
        monkeypatch.setattr(spellbook, "find_in_deck", boom)
        result = combos_analysis.analyse(self._deck())
        assert result["available"] is False
        assert result["complete"] == [] and result["near_miss"] == []


class TestSpellbookMatching:
    def test_complete_and_near_miss_are_separated(self, monkeypatch):
        combo_have = spellbook.Combo(id="1", cards=["A", "B"], identity="")
        combo_near = spellbook.Combo(id="2", cards=["A", "C"], identity="")
        monkeypatch.setattr(
            spellbook, "combos_using", lambda name, **k: [combo_have, combo_near]
        )
        found = spellbook.find_in_deck({"a", "b"}, ["A"])
        assert [c.id for c in found["complete"]] == ["1"]
        assert [c.id for c in found["near_miss"]] == ["2"]

    def test_two_missing_cards_is_not_a_near_miss(self, monkeypatch):
        far = spellbook.Combo(id="9", cards=["A", "X", "Y"], identity="")
        monkeypatch.setattr(spellbook, "combos_using", lambda name, **k: [far])
        found = spellbook.find_in_deck({"a"}, ["A"])
        assert found["complete"] == [] and found["near_miss"] == []
