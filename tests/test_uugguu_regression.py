"""Regression tests for the second Uugguu failure: not understanding the deck.

After the Ayara fixes, the tool still recommended cutting Aeve and March of the
World Ooze (the deck's biggest typal payoffs) over a fixable green shortfall,
cutting Ashnod's Altar and Birthing Pod as "oversupplied" on a death-trigger
commander, and cutting Umori and Hecteyes — literal Oozes in the Ooze deck —
because their text tags matched no cluster. The word "Ooze" appeared nowhere in
any generated file. These tests hold the tool to the deck's idea.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mtgai import analysis
from mtgai.analysis import engine, report
from mtgai.model import Deck
from mtgai.sources import archidekt

FIXTURES = Path(__file__).parent / "fixtures"

# Engine pieces the tool must never offer as cuts: the two typal payoffs the
# mana base strains under (fix the mana instead), the free sacrifice outlets a
# death-trigger commander runs on, and the two type-line Oozes whose tags say
# nothing.
NEVER_CUT = [
    "Aeve, Progenitor Ooze",
    "March of the World Ooze",
    "Ashnod's Altar",
    "Birthing Pod",
    "Umori, the Collector",
    "Hecteyes",
]


@pytest.fixture
def uugguu() -> Deck:
    payload = json.loads((FIXTURES / "archidekt_uugguu.json").read_text())
    return archidekt.normalise(payload, "uugguu-fixture", enrich=False)


@pytest.fixture
def result(uugguu) -> dict:
    return analysis.analyse(uugguu, offline=True)


class TestTheDeckIsUnderstood:
    def test_the_tribe_is_detected(self, uugguu):
        assert engine.commander_tribe(uugguu) == "Ooze"

    def test_the_archetype_names_the_tribe(self, result):
        assert "Ooze" in result["engine"]["archetype"]

    def test_the_tribe_census_counts_every_kind_of_member(self, result):
        census = result["engine"]["tribe_census"]
        assert census["tribe"] == "Ooze"
        assert census["true_type"] >= 10
        assert census["changelings"] >= 3
        # Clones become Oozes when they copy one — the CLAUDE.md rule in code.
        assert census["conditional_copies"] > 0

    def test_type_line_oozes_participate_in_the_typal_cluster(self, uugguu):
        engine.analyse(uugguu)
        for name in ("Umori, the Collector", "Hecteyes"):
            card = uugguu.find(name)
            assert card is not None
            assert "typal" in card.engine_participation, (
                f"{name} is an Ooze in the Ooze deck; being the tribe is "
                "participation, whatever its text tags say"
            )


class TestJudgementFollowsTheCommander:
    def test_sacrifice_is_not_oversupplied(self, result):
        over = {e["category"] for e in result["engine"]["oversupplied"]}
        assert "sacrifice" not in over

    def test_engine_pieces_are_never_cut(self, uugguu, result):
        cuts = {c["name"] for c in report._build_cuts(uugguu, result)}
        offered = [n for n in NEVER_CUT if n in cuts]
        assert not offered, f"offered engine pieces as cuts: {offered}"

    def test_the_green_shortfall_is_a_mana_fix_not_a_cut(self, result):
        fixes = {f["color"]: f for f in result["engine"]["mana_fixes"]}
        assert "G" in fixes, "Aeve and March strain green; the answer is sources"
        assert fixes["G"]["delta"] > 0
        assert "Aeve, Progenitor Ooze" in fixes["G"]["driven_by"]

    def test_mana_fixes_lead_the_headline(self, result):
        assert any("Fix the mana first" in line for line in result["headline"])

    def test_sole_drivers_and_three_colour_costs_still_cut(self, result):
        """The demotion is drawn precisely: Ayara and Mimeoplasm stay cuts."""
        cut_names = {f["name"] for f in result["engine"]["castability_cuts"]}
        assert "Ayara, First of Locthwain" in cut_names
        assert "The Mimeoplasm" in cut_names

    def test_a_sole_driver_cut_offers_the_mana_alternative(self, result):
        ayara = next(
            f for f in result["engine"]["castability_cuts"]
            if f["name"] == "Ayara, First of Locthwain"
        )
        assert "alternative" in ayara
        assert "Black source" in ayara["alternative"]


class TestRolesAreReconciled:
    def test_regex_findings_contradicted_by_tags_are_dropped(self, result):
        """The old report said "5 removal (want 6-10)" beside "removal 14"."""
        for finding in result["roles"]["findings"]:
            assert finding["role"] != "removal", (
                "tags count 14 removal; a regex-derived 'too few' is noise"
            )

    def test_tag_counts_ride_along_for_the_report(self, result):
        assert result["roles"]["tag_counts"].get("removal", 0) >= 10
