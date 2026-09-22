"""intent.md: the file where Nicolas teaches the tool what a deck is about.

The contract under test: the front matter round-trips, hand edits and unknown
keys survive programmatic writes, a malformed line warns instead of breaking
analysis, refresh never rewrites an existing file, and the analysis actually
obeys what was declared — sacred cards are never cuts, declared targets replace
generic ones.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mtgai import analysis, deckfolder, intent as intent_mod, service
from mtgai.analysis import engine, report
from mtgai.intent import DeckIntent, apply_assignments, parse_intent, render_intent
from mtgai.model import Deck
from mtgai.sources import archidekt

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def uugguu() -> Deck:
    payload = json.loads((FIXTURES / "archidekt_uugguu.json").read_text())
    return archidekt.normalise(payload, "uugguu-fixture", enrich=False)


class TestRoundTrip:
    def test_full_round_trip(self):
        original = DeckIntent(
            archetype="Ooze typal aristocrats",
            tribe="Ooze",
            commander_role=["payoff", "force-multiplier"],
            win_conditions=["go wide with token copies", "drain the table"],
            core_cards=["Ashnod's Altar", "Aeve, Progenitor Ooze"],
            flexible_cards=["Harmonized Crescendo"],
            core_categories={"sacrifice": 16, "ramp": 14},
            budget_per_card=20.0,
            power_bracket=3,
            meta_notes="casual pod, no MLD",
            prose="The deck wants a stream of nontoken Oozes dying.",
            source="interview",
            updated_at="2026-08-29T00:00:00+00:00",
        )
        parsed, warnings = parse_intent(render_intent(original))
        assert warnings == []
        assert parsed.to_dict() == original.to_dict()

    def test_unknown_keys_survive_a_programmatic_edit(self):
        text = (
            "---\n"
            "schema_version: 1\n"
            "archetype: Ooze stuff\n"
            "favourite_card: Hecteyes\n"
            "---\n"
            "\nMy prose stays.\n"
        )
        parsed, _ = parse_intent(text)
        parsed, _ = apply_assignments(parsed, {"tribe": "Ooze"})
        rendered = render_intent(parsed)
        assert "favourite_card: Hecteyes" in rendered
        assert "My prose stays." in rendered
        assert "tribe: Ooze" in rendered

    def test_a_malformed_line_warns_and_everything_else_parses(self):
        text = (
            "---\n"
            "archetype: Oozes\n"
            "this line has no colon\n"
            "core_categories:\n"
            "  ramp: plenty\n"
            "  draw: 15\n"
            "---\n"
            "prose\n"
        )
        parsed, warnings = parse_intent(text)
        assert parsed.archetype == "Oozes"
        assert parsed.core_categories == {"draw": 15}
        assert parsed.prose.strip() == "prose"
        assert len(warnings) == 2

    def test_a_file_with_no_front_matter_is_all_prose(self):
        parsed, warnings = parse_intent("Just my thoughts.\n")
        assert parsed.prose.strip() == "Just my thoughts."
        assert warnings


class TestAssignments:
    def test_list_add_and_remove(self):
        base = DeckIntent(core_cards=["Ashnod's Altar"])
        base, _ = apply_assignments(base, {"core_cards": "+Birthing Pod"})
        assert base.core_cards == ["Ashnod's Altar", "Birthing Pod"]
        base, _ = apply_assignments(base, {"core_cards": "-ashnod's altar"})
        assert base.core_cards == ["Birthing Pod"]

    def test_dict_assignment(self):
        base = DeckIntent()
        base, warnings = apply_assignments(
            base, {"core_categories": "ramp=20, draw:15, tutor=lots"}
        )
        assert base.core_categories == {"ramp": 20, "draw": 15}
        assert len(warnings) == 1

    def test_unknown_field_warns(self):
        _, warnings = apply_assignments(DeckIntent(), {"vibe": "aggressive"})
        assert warnings


class TestFileContract:
    def test_refresh_never_rewrites_an_existing_intent(self, uugguu, monkeypatch):
        payload = json.loads((FIXTURES / "archidekt_uugguu.json").read_text())
        monkeypatch.setattr(archidekt, "fetch_raw", lambda deck_id, use_cache=True: payload)

        service.add_deck("25569889", offline=True)
        folder = deckfolder.resolve("uugguu")
        assert not folder.intent_path.exists(), "add must not create intent.md"

        folder.intent_path.write_text("---\narchetype: mine\n---\nhands off\n")
        service.refresh_deck("uugguu", offline=True)
        assert "hands off" in folder.intent_path.read_text()

    def test_init_refuses_to_overwrite(self, uugguu, monkeypatch):
        payload = json.loads((FIXTURES / "archidekt_uugguu.json").read_text())
        monkeypatch.setattr(archidekt, "fetch_raw", lambda deck_id, use_cache=True: payload)
        service.add_deck("25569889", offline=True)

        service.deck_intent_init("uugguu")
        folder = deckfolder.resolve("uugguu")
        first = folder.intent_path.read_text()
        assert "Seeded from inference" in first

        with pytest.raises(ValueError):
            service.deck_intent_init("uugguu")

    def test_set_creates_then_edits_and_analysis_obeys(self, monkeypatch):
        payload = json.loads((FIXTURES / "archidekt_uugguu.json").read_text())
        monkeypatch.setattr(archidekt, "fetch_raw", lambda deck_id, use_cache=True: payload)
        service.add_deck("25569889", offline=True)

        shown = service.deck_intent_set(
            "uugguu",
            {"core_cards": "Pitiless Plunderer", "core_categories": "ramp=20"},
        )
        assert shown["intent"]["core_cards"] == ["Pitiless Plunderer"]
        assert shown["intent"]["source"] == "interview"

        result = service.analyse_deck("uugguu", offline=True)
        over = {e["category"] for e in result["engine"]["oversupplied"]}
        assert "ramp" not in over, "declared target of 20 silences the ramp flag"

        folder = deckfolder.resolve("uugguu")
        deck = folder.read_deck()
        cuts = {c["name"] for c in report._build_cuts(deck, result)}
        assert "Pitiless Plunderer" not in cuts, "sacred cards are never cuts"


class TestIntentInAnalysis:
    def test_sacred_card_is_refused_as_a_cut(self, uugguu):
        declared = DeckIntent(core_cards=["Pitiless Plunderer"])
        result = analysis.analyse(uugguu, offline=True, intent=declared)
        cuts = {c["name"] for c in report._build_cuts(uugguu, result)}
        assert "Pitiless Plunderer" not in cuts

    def test_declared_targets_replace_generic_ones(self, uugguu):
        declared = DeckIntent(core_categories={"ramp": 20})
        over = {
            e["category"]
            for e in engine.oversupplied(uugguu, overrides=declared.core_categories)
        }
        assert "ramp" not in over

    def test_declared_archetype_leads_the_inferred_one(self, uugguu):
        declared = DeckIntent(archetype="Uugguu clone aristocrats")
        result = engine.analyse(uugguu, declared)
        assert result["archetype"].startswith("Uugguu clone aristocrats")
        assert "inferred:" in result["archetype"]

    def test_flexible_cards_are_offered_first(self, uugguu):
        declared = DeckIntent(flexible_cards=["Springleaf Parade"])
        result = analysis.analyse(uugguu, offline=True, intent=declared)
        ramp_cuts = [
            c
            for c in report._build_cuts(uugguu, result)
            if c["evidence"] == "oversupplied: ramp"
        ]
        assert ramp_cuts and ramp_cuts[0]["name"] == "Springleaf Parade"


class TestCommanderClassification:
    def test_uugguu_is_a_payoff_and_multiplier(self, uugguu):
        role = engine.classify_commander(uugguu)
        assert "payoff" in role["roles"]
        assert "force-multiplier" in role["roles"]
        assert any("sacrifice outlets" in s for s in role["supplies"])

    def test_description_and_deck_tags_are_ingested(self):
        payload = json.loads((FIXTURES / "archidekt_uugguu.json").read_text())
        payload = dict(payload)
        payload["description"] = "Sac oozes, copy them, drain the table."
        payload["deckTags"] = ["aristocrats", {"name": "typal"}]
        deck = archidekt.normalise(payload, "uugguu-fixture", enrich=False)
        assert deck.description.startswith("Sac oozes")
        assert deck.deck_tags == ["aristocrats", "typal"]
        restored = Deck.from_dict(json.loads(json.dumps(deck.to_dict())))
        assert restored.description == deck.description
        assert restored.deck_tags == deck.deck_tags
