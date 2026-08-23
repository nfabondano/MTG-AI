"""Regression tests for the Ayara failure.

The tool once proposed cutting Ayara, Yawgmoth, Pitiless Plunderer and Species
Specialist from a Uugguu deck — an entire aristocrats engine — for the sole
reason that they sat outside a truncated EDHREC list. The justification given
was that Ayara "won't trigger, most of your creatures are blue clones", which is
backwards: Uugguu is a black creature, so its clones and its tokens are black.

Nicolas then fixed the deck by hand, and cut Ayara, The Mimeoplasm, Pitiless
Plunderer and Kindred Dominance — for real reasons the tool had the data for and
never used. These tests hold the tool to those reasons.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mtgai import analysis, tags
from mtgai.analysis import edhrec_delta, engine, report
from mtgai.model import CardEntry, Deck
from mtgai.sources import archidekt

FIXTURES = Path(__file__).parent / "fixtures"

# What Nicolas actually cut.
ACTUAL_CUTS = [
    "Ayara, First of Locthwain",
    "The Mimeoplasm",
    "Pitiless Plunderer",
    "Kindred Dominance",
]

# Engine pieces the old tool wrongly flagged as "unusual inclusions".
ENGINE_PIECES = [
    "Yawgmoth, Thran Physician",
    "Species Specialist",
    "Ashnod's Altar",
    "Phyrexian Altar",
]


@pytest.fixture
def uugguu() -> Deck:
    payload = json.loads((FIXTURES / "archidekt_uugguu.json").read_text())
    return archidekt.normalise(payload, "uugguu-fixture", enrich=False)


class TestTagsFlowThrough:
    def test_archidekt_supplies_tags_for_every_card(self, uugguu):
        """Tags arrive free in the payload we already download."""
        tagged = [c for c in uugguu.cards if c.tags]
        assert len(tagged) == len(uugguu.cards)

    def test_the_commander_states_the_engine(self, uugguu):
        commander = uugguu.find("Uugguu, the Omniplasm")
        found = tags.card_categories(commander)
        # copy-creature, death trigger, repeatable creature tokens, typal-ooze
        assert {"copy", "death-trigger", "tokens", "typal"} <= found

    def test_flavour_tags_are_not_functional(self):
        assert tags.categories_for("alliteration") == []
        assert tags.categories_for("single english word name") == []
        assert tags.categories_for("cycle-eld-court-leader") == []

    def test_both_sources_normalise_to_one_key(self):
        """Archidekt writes spaces where Scryfall writes hyphens."""
        assert tags.slugify("sacrifice outlet-creature") == "sacrifice-outlet-creature"
        assert tags.slugify("sacrifice outlet-creature") == tags.slugify(
            "sacrifice-outlet-creature"
        )

    def test_tags_survive_the_deck_json_round_trip(self, uugguu):
        restored = Deck.from_dict(json.loads(json.dumps(uugguu.to_dict())))
        assert restored.find("Ayara, First of Locthwain").tags


class TestEngineDetection:
    def test_archetype_is_recognised(self, uugguu):
        result = engine.analyse(uugguu)
        # A clone/Ooze/sacrifice deck, whatever EDHREC has heard of.
        assert {"copy", "sacrifice"} & set(result["core"])

    def test_commander_wants_sacrifice_outlets(self, uugguu):
        """A death-trigger commander makes sacrifice outlets core, not filler."""
        wants = engine.commander_wants(uugguu)
        assert "sacrifice" in wants
        assert "copy" in wants

    def test_engine_pieces_participate(self, uugguu):
        """The cards the old tool called junk are load-bearing."""
        engine.analyse(uugguu)
        for name in ENGINE_PIECES:
            card = uugguu.find(name)
            if card is None:
                continue
            assert card.engine_participation, f"{name} reads as doing nothing"

    def test_lands_never_participate(self, uugguu):
        engine.analyse(uugguu)
        assert all(not c.engine_participation for c in uugguu.lands)


class TestCastability:
    """The signal that actually explains the cuts, and that the tool ignored."""

    def test_ayara_is_the_sole_driver_of_the_black_requirement(self, uugguu):
        findings = {f["name"]: f for f in engine.castability(uugguu)}
        ayara = findings.get("Ayara, First of Locthwain")
        assert ayara is not None, "BBB in a three-colour deck must register"
        assert ayara["sole_driver"], (
            "nothing else asks for three black pips, so cutting Ayara relaxes "
            "the whole mana base — that is the real reason she goes"
        )

    def test_mimeoplasm_flagged_for_needing_three_colours(self, uugguu):
        findings = {f["name"]: f for f in engine.castability(uugguu)}
        mimeo = findings.get("The Mimeoplasm")
        assert mimeo is not None
        assert mimeo["distinct_colors"] >= 3

    def test_a_one_source_shortfall_is_not_a_cut(self):
        """A small shortfall shared by many cards means add sources, not cut six.

        Left unfiltered this fired on eight cards at once, which is the same
        false-signal failure in a new costume.
        """
        cards = [
            CardEntry(name="Boss", type_line="Legendary Creature", mana_cost="{U}{B}",
                      color_identity=["U", "B"], is_commander=True),
            *[
                CardEntry(name=f"Blue {i}", type_line="Creature", mana_cost="{U}{U}",
                          color_identity=["U"])
                for i in range(6)
            ],
            CardEntry(name="Island", quantity=18, type_line="Basic Land — Island",
                      mana_production={"U": 1}, color_identity=["U"]),
        ]
        deck = Deck(slug="t", name="T", archidekt_id=1, cards=cards)
        result = engine.analyse(deck)
        assert len(result["castability"]) > len(result["castability_cuts"])
        assert not result["castability_cuts"], "a 2-source gap is a mana-base note"

    def test_commander_is_never_a_cut_candidate(self, uugguu):
        names = {f["name"] for f in engine.castability(uugguu)}
        assert "Uugguu, the Omniplasm" not in names


class TestOversupply:
    def test_flags_a_cluster_holding_too_much(self, uugguu):
        over = {e["category"] for e in engine.oversupplied(uugguu)}
        assert "sacrifice" in over, "14 sacrifice outlets is more than a deck needs"


class TestEdhrecIsNoLongerEvidence:
    """Absence from a truncated popularity list must never justify anything."""

    def test_absent_cards_are_not_flagged(self, uugguu, monkeypatch):
        from mtgai.sources import edhrec

        # EDHREC knows one card and nothing else in the deck.
        data = edhrec.CommanderData(slug="uugguu-the-omniplasm", found=True)
        data.recommendations = [
            edhrec.Recommendation(name="Ashnod's Altar", num_decks=90, potential_decks=100)
        ]
        monkeypatch.setattr(edhrec, "commander_for", lambda names: data)

        result = edhrec_delta.analyse(uugguu)
        flagged = {e["name"] for e in result["off_meta"]}
        for name in ENGINE_PIECES + ACTUAL_CUTS:
            assert name not in flagged, f"{name} flagged purely for being absent"

    def test_every_flag_carries_a_measured_rate(self, uugguu, monkeypatch):
        from mtgai.sources import edhrec

        data = edhrec.CommanderData(slug="s", found=True)
        data.recommendations = [
            edhrec.Recommendation(name=c.name, num_decks=1, potential_decks=1000)
            for c in uugguu.cards
            if not c.is_land
        ]
        monkeypatch.setattr(edhrec, "commander_for", lambda names: data)

        result = edhrec_delta.analyse(uugguu)
        for entry in result["off_meta"]:
            assert entry["inclusion"] is not None
            assert "not on any EDHREC list" not in entry["reason"]

    def test_thin_coverage_suppresses_the_comparison(self, uugguu, monkeypatch):
        from mtgai.sources import edhrec

        data = edhrec.CommanderData(slug="s", found=True)
        data.recommendations = [
            edhrec.Recommendation(name="Ashnod's Altar", num_decks=1, potential_decks=1000)
        ]
        monkeypatch.setattr(edhrec, "commander_for", lambda names: data)
        assert edhrec_delta.analyse(uugguu)["off_meta"] == []


class TestCutsAreEarned:
    def test_finds_what_nicolas_actually_cut(self, uugguu):
        """The four real cuts must all surface, each with its own reason."""
        result = analysis.analyse(uugguu, offline=True)
        cuts = {c["name"]: c for c in report._build_cuts(uugguu, result)}

        missing = [n for n in ACTUAL_CUTS if n not in cuts]
        assert not missing, f"failed to surface real cuts: {missing}"

        # And each for a deck-internal reason, not popularity.
        assert cuts["Ayara, First of Locthwain"]["evidence"] == "castability"
        assert cuts["The Mimeoplasm"]["evidence"] == "castability"

    def test_no_cut_is_justified_by_popularity(self, uugguu):
        result = analysis.analyse(uugguu, offline=True)
        for cut in report._build_cuts(uugguu, result):
            assert "EDHREC" not in cut["why"]
            assert "not on any" not in cut["why"]

    def test_every_cut_carries_evidence(self, uugguu):
        result = analysis.analyse(uugguu, offline=True)
        for cut in report._build_cuts(uugguu, result):
            assert cut["evidence"], f"{cut['name']} proposed with no evidence"
            assert cut["why"]

    def test_combo_pieces_are_protected(self, uugguu):
        result = analysis.analyse(uugguu, offline=True)
        result["combos"] = {
            "available": True,
            "complete": [{"cards": ["Ayara, First of Locthwain"], "produces": ["win"]}],
            "near_miss": [],
        }
        cuts = {c["name"] for c in report._build_cuts(uugguu, result)}
        assert "Ayara, First of Locthwain" not in cuts

    def test_weak_signals_need_loose(self, uugguu):
        result = analysis.analyse(uugguu, offline=True)
        result["edhrec"] = {
            "available": True,
            "off_meta": [{"name": "Species Specialist", "inclusion": 0.01,
                          "reason": "played in 1% of decks", "mana_value": 4}],
            "missing_staples": [], "missing_synergy": [],
        }
        strict = {c["name"] for c in report._build_cuts(uugguu, result)}
        loose = {c["name"] for c in report._build_cuts(uugguu, result, loose=True)}
        assert "Species Specialist" not in strict
        assert "Species Specialist" in loose
