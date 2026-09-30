"""`mtg deck trim` — "it has to be 100: which ones go, and a few spares".

That question was answered by hand every time, with the tool's own cut list
half ignored. These tests pin the answer to the real decks it was asked about,
so it has to be one Nicolas would not throw away.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import REAL_DECKS, load_fixture
from mtgai import analysis, deckfolder, service
from mtgai.analysis import combos, engine, report, trim
from mtgai.intent import DeckIntent
from mtgai.model import CardEntry, Deck
from mtgai.sources import archidekt, spellbook

FIXTURES = Path(__file__).parent / "fixtures"

# Same list as test_uugguu_regression.py: cards Nicolas would never cut.
UUGGUU_NEVER_CUT = [
    "Aeve, Progenitor Ooze",
    "March of the World Ooze",
    "Ashnod's Altar",
    "Birthing Pod",
    "Umori, the Collector",
    "Hecteyes",
]

# The advice thrown away on Felisa: Teferi's, Lae'zel and Demonic Tutor for
# false reasons, Broodmoth and Valkyrie's Call because their engine role
# inflated the protection count.
FELISA_THROWN_AWAY = [
    "Teferi's Protection",
    "Lae'zel, Vlaakith's Champion",
    "Demonic Tutor",
    "Luminous Broodmoth",
    "Valkyrie's Call",
]


def _with_combos(deck: Deck, monkeypatch, recorded: dict, intent=None) -> dict:
    """The offline analysis plus the combos Spellbook recorded for this deck."""
    result = analysis.analyse(deck, offline=True, intent=intent)
    monkeypatch.setattr(spellbook, "post_json", lambda url, body, **k: recorded)
    result["combos"] = combos.analyse(deck)
    return result


def _picked(plan: dict) -> list[str]:
    return [p["name"] for p in plan["cuts"] + plan["extras"]]


@pytest.fixture
def uugguu() -> Deck:
    payload = json.loads((FIXTURES / "archidekt_uugguu.json").read_text())
    return archidekt.normalise(payload, "uugguu-fixture", enrich=False)


class TestFelisa:
    """106 cards: six cuts, three spares, and nothing the deck runs on."""

    @pytest.fixture
    def result(self, felisa, monkeypatch, spellbook_fmc):
        return _with_combos(felisa, monkeypatch, spellbook_fmc["felisa"])

    def test_six_cuts_and_three_spares(self, felisa, result):
        plan = trim.plan_trim(felisa, result)
        assert plan["need"] == 6
        assert len(plan["cuts"]) == 6
        assert len(plan["extras"]) == 3
        assert plan["after"]["total"] == 100

    def test_never_a_land_the_commander_or_a_combo_piece(self, felisa, result):
        plan = trim.plan_trim(felisa, result)
        pieces = {n for combo in result["combos"]["complete"] for n in combo["cards"]}
        assert "Cathars' Crusade" in pieces  # the recorded combos are really there
        for name in _picked(plan):
            card = felisa.find(name)
            assert not card.is_land, name
            assert not card.is_commander, name
            assert name not in pieces, name
        assert all(plan["protected"].get(n) == "combo piece" for n in pieces)

    def test_picks_are_distinct(self, felisa, result):
        names = _picked(trim.plan_trim(felisa, result))
        assert len(names) == len(set(names))

    def test_the_answer_is_stable(self, felisa, result):
        assert trim.plan_trim(felisa, result) == trim.plan_trim(felisa, result)

    def test_none_of_the_advice_that_was_thrown_away(self, felisa, result):
        plan = trim.plan_trim(felisa, result)
        assert not set(_picked(plan)) & set(FELISA_THROWN_AWAY)
        for pick in plan["cuts"] + plan["extras"]:
            assert "over-represented" not in pick["reason"]
            assert "protection" not in pick["evidence"]

    def test_every_pick_says_why(self, felisa, result):
        for pick in trim.plan_trim(felisa, result)["cuts"]:
            assert pick["reason"] and pick["evidence"]
            assert pick["tier"] in trim.TIERS

    def test_judgement_calls_say_so(self, felisa, result):
        """Felisa is all engine plus support at its minimum: whatever goes is
        a judgement call, and the plan has to say it rather than dress it up."""
        plan = trim.plan_trim(felisa, result)
        calls = [p for p in plan["cuts"] if p["evidence"].startswith("judgement call")]
        assert calls
        assert any("judgement call" in note for note in plan["notes"])

    def test_popularity_is_never_the_reason(self, felisa, result):
        """It may order cards the deck already ranks equal — and then it is
        the weak signal, never the reason."""
        result["edhrec"] = {"inclusion": {"guiding hydra": 0.05, "scholar of new horizons": 0.2}}
        plan = trim.plan_trim(felisa, result)
        assert plan["cuts"][0]["name"] == "Guiding Hydra"
        for pick in plan["cuts"]:
            assert "played" not in pick["reason"] and "%" not in pick["reason"]
        assert "5%" in plan["cuts"][0]["weak_signal"]

    def test_declared_untouchable_is_never_offered(self, felisa, monkeypatch, spellbook_fmc):
        base = _with_combos(felisa, monkeypatch, spellbook_fmc["felisa"])
        first = trim.plan_trim(felisa, base)["cuts"][0]["name"]
        intent = DeckIntent(core_cards=[first])
        plan = trim.plan_trim(
            felisa, _with_combos(felisa, monkeypatch, spellbook_fmc["felisa"], intent=intent)
        )
        assert first not in _picked(plan)
        assert plan["protected"][first] == "declared untouchable"

    def test_declared_flexible_goes_first(self, felisa, monkeypatch, spellbook_fmc):
        intent = DeckIntent(flexible_cards=["Esper Sentinel", "Dark Ritual"])
        plan = trim.plan_trim(
            felisa, _with_combos(felisa, monkeypatch, spellbook_fmc["felisa"], intent=intent)
        )
        assert {p["name"] for p in plan["cuts"][:2]} == {"Esper Sentinel", "Dark Ritual"}
        assert {p["tier"] for p in plan["cuts"][:2]} == {"declared"}

    def test_offline_says_what_it_could_not_check(self, felisa):
        plan = trim.plan_trim(felisa, analysis.analyse(felisa, offline=True))
        assert any("combo pieces are not protected" in note for note in plan["notes"])


class TestEquipmentsAtBracketTwo:
    """103 cards, declared bracket 2 on Archidekt."""

    @pytest.fixture
    def result(self, equipments):
        return analysis.analyse(equipments, offline=True)

    def test_three_cuts_and_bracket_two_after(self, equipments, result):
        for plan in (trim.plan_trim(equipments, result, max_bracket=2),
                     trim.plan_trim(equipments, result)):
            assert plan["max_bracket"] == 2
            assert len(plan["cuts"]) == 3
            assert plan["after"]["total"] == 100
            assert plan["after"]["bracket"] <= 2
            assert plan["required_for_bracket"] == []

    def test_where_the_bracket_came_from(self, equipments, result):
        assert trim.plan_trim(equipments, result)["bracket_source"] == "Archidekt"
        assert trim.plan_trim(equipments, result, max_bracket=2)["bracket_source"] == "requested"

    def test_the_equipment_is_not_an_orphan_and_tifa_stays(self, equipments, result):
        plan = trim.plan_trim(equipments, result, extra=6)
        assert all(p["tier"] != "orphan" for p in plan["cuts"] + plan["extras"])
        assert "Tifa, Martial Artist" not in _picked(plan)

    def test_a_voltron_deck_keeps_its_protection_spells(self, equipments, result):
        """Cloud asks for protection, so its minimum scales with its target:
        four protection spells are under the floor, not a surplus. The generic
        floor of two offered Clever Concealment and Heroic Intervention as
        spares — the same cut Nicolas was told by hand to ignore."""
        spells = {"Clever Concealment", "Heroic Intervention", "Boros Charm", "Flawless Maneuver"}
        protection = next(
            e for e in result["engine"]["oversupplied"] if e["category"] == "protection"
        )
        assert protection["target_low"] == 6 and protection["cuttable"] == 0
        plan = trim.plan_trim(equipments, result, extra=6)
        assert not set(_picked(plan)) & spells

    def test_wanted_ramp_is_not_held_to_a_doubled_floor(self, equipments):
        from mtgai.analysis import engine as engine_mod

        wants = ["ramp", "protection"]
        assert engine_mod.category_bounds("ramp", wants) == (12, 24)
        assert engine_mod.category_bounds("protection", wants) == (6, 12)
        assert engine_mod.category_bounds("removal", wants) == (6, 10)
        assert engine_mod.category_bounds("ramp", wants, {"ramp": 10}) == (8, 10)

    def test_self_discounting_spells_are_not_the_most_expensive(self, equipments):
        assert engine.effective_cost(equipments.find("Excalibur, Sword of Eden")) <= 3


class TestUugguu:
    """What Nicolas really cut from the Ooze deck, in the order of its reasons."""

    def test_the_real_cuts_come_first(self, uugguu):
        result = analysis.analyse(uugguu, offline=True)
        plan = trim.plan_trim(uugguu, result, target=uugguu.total_cards - 3)
        assert [p["name"] for p in plan["cuts"]] == [
            "Ayara, First of Locthwain",
            "The Mimeoplasm",
            "Pitiless Plunderer",
        ]

    def test_what_he_would_never_cut_stays(self, uugguu):
        result = analysis.analyse(uugguu, offline=True)
        for target in (uugguu.total_cards - 3, uugguu.total_cards - 8):
            plan = trim.plan_trim(uugguu, result, target=target, extra=3)
            assert not set(_picked(plan)) & set(UUGGUU_NEVER_CUT)

    def test_text_that_names_the_tribe_is_typal(self, uugguu):
        march = uugguu.find("March of the World Ooze")
        assert engine.serves_tribe(march, "Ooze")
        assert not engine.is_tribe_member(march, "Ooze")


class TestNamedWinConditions:
    def test_cards_named_in_the_win_conditions_are_protected(self, niv):
        intent = DeckIntent(
            win_conditions=["Aetherflux Reservoir + Exquisite Blood or Bloodthirsty Conqueror"],
        )
        result = analysis.analyse(niv, offline=True, intent=intent)
        plan = trim.plan_trim(niv, result, target=90, extra=5)
        for name in ("Aetherflux Reservoir", "Exquisite Blood"):
            assert name not in _picked(plan)
            assert plan["protected"][name] == "named in the declared win conditions"


def _deck(changers: int, fillers: int) -> Deck:
    cards = [
        CardEntry(name="The Commander", type_line="Legendary Creature — Human",
                  is_commander=True, color_identity=["W"], tags=["draw"]),
    ]
    cards += [
        CardEntry(name=f"Changer {i}", type_line="Artifact", mana_value=i + 1,
                  is_game_changer=True, tags=["ramp"])
        for i in range(changers)
    ]
    cards += [
        CardEntry(name=f"Plains {i}", type_line="Basic Land — Plains") for i in range(fillers)
    ]
    for card in cards[-fillers:]:
        card.quantity = 1
    return Deck(slug="t", name="T", archidekt_id=1, cards=cards)


class TestBracketCuts:
    def test_surplus_game_changers_are_required(self):
        deck = _deck(changers=5, fillers=94)
        assert deck.total_cards == 100
        result = analysis.analyse(deck, offline=True)
        plan = trim.plan_trim(deck, result, max_bracket=3)
        assert plan["need"] == 0
        assert [p["name"] for p in plan["cuts"]] == ["Changer 4", "Changer 3"]
        assert all(p["mandatory"] and p["tier"] == "bracket" for p in plan["cuts"])
        assert plan["required_for_bracket"] == ["Changer 4", "Changer 3"]
        assert plan["below_target"]
        assert plan["after"]["bracket"] <= 3
        assert any("add 2 cards" in note for note in plan["notes"])

    def test_the_bracket_cut_is_counted_toward_the_hundred(self):
        deck = _deck(changers=5, fillers=96)  # 102 cards, 2 Game Changers too many
        plan = trim.plan_trim(deck, analysis.analyse(deck, offline=True), max_bracket=3, extra=0)
        assert plan["need"] == 2
        assert len(plan["cuts"]) == 2
        assert not plan["below_target"]


class TestNothingToDo:
    def test_a_deck_at_size_needs_no_cuts(self, niv):
        plan = trim.plan_trim(niv, analysis.analyse(niv, offline=True))
        assert plan["need"] == 0
        assert plan["cuts"] == []
        assert "nothing needs to go" in plan["notes"][0]
        assert "Nothing to cut" not in report.render_trim(plan) or plan["notes"]


class TestRenderedAnswer:
    def test_markdown_numbers_the_cuts_and_lists_spares(self, felisa, monkeypatch, spellbook_fmc):
        plan = trim.plan_trim(felisa, _with_combos(felisa, monkeypatch, spellbook_fmc["felisa"]))
        text = report.render_trim(plan)
        assert text.startswith("## To reach 100")
        assert "1. **" in text and "6. **" in text
        assert "Extra candidates" in text
        assert "After the cuts: 100 cards" in text


class TestService:
    def _serve(self, monkeypatch, key: str) -> None:
        payload = load_fixture(REAL_DECKS[key][0])
        monkeypatch.setattr(archidekt, "fetch_raw", lambda deck_id, use_cache=True: payload)

    def test_trim_deck_answers_without_writing(self, monkeypatch):
        self._serve(monkeypatch, "felisa")
        added = service.add_deck("6313712", offline=True)
        folder = deckfolder.folder_for(added["slug"])
        before = {p.name: p.stat().st_mtime_ns for p in folder.path.iterdir()}
        plan = service.trim_deck(added["slug"], offline=True)
        assert len(plan["cuts"]) == 6
        assert plan["markdown"].startswith("# To reach 100")
        assert {p.name: p.stat().st_mtime_ns for p in folder.path.iterdir()} == before

    def test_oversized_suggestions_lead_with_the_trim(self, monkeypatch):
        self._serve(monkeypatch, "felisa")
        added = service.add_deck("6313712", offline=True)
        text = deckfolder.folder_for(added["slug"]).suggestions_path.read_text()
        assert "## To reach 100" in text


class TestOversupplyRespectsOtherJobs:
    def test_a_surplus_removal_spell_that_is_one_of_two_wipes_stays(self):
        """Removal was oversupplied; the most expensive removal card was also
        one of the deck's two sweepers, and cutting it for the removal surplus
        took the wipes below their minimum."""
        cards = [
            CardEntry(name="The Commander", type_line="Legendary Creature — Human",
                      is_commander=True, tags=["draw"]),
            CardEntry(name="Big Wipe", type_line="Sorcery", mana_value=7,
                      tags=["removal-creature", "sweeper"]),
            CardEntry(name="Small Wipe", type_line="Sorcery", mana_value=4, tags=["sweeper"]),
        ]
        cards += [
            CardEntry(name=f"Removal {i}", type_line="Instant", mana_value=1 + i % 3,
                      tags=["removal-creature"])
            for i in range(13)
        ]
        cards += [
            CardEntry(name=f"Plains {i}", type_line="Basic Land — Plains") for i in range(86)
        ]
        deck = Deck(slug="t", name="T", archidekt_id=1, cards=cards)
        assert deck.total_cards == 102
        plan = trim.plan_trim(deck, analysis.analyse(deck, offline=True), extra=0)
        assert [p["tier"] for p in plan["cuts"]] == ["oversupply", "oversupply"]
        assert "Big Wipe" not in _picked(plan)


class TestSelfDiscountingSpells:
    def test_they_are_costed_as_three_drops(self):
        act = CardEntry(
            name="Blasphemous Act", mana_value=9,
            oracle_text="This spell costs {1} less to cast for each creature on the "
            "battlefield.\nBlasphemous Act deals 13 damage to each creature.",
        )
        cruise = CardEntry(
            name="Treasure Cruise", mana_value=8,
            oracle_text="Delve (Each card you exile from your graveyard while casting "
            "this spell pays for {1}.)\nDraw three cards.",
        )
        plain = CardEntry(name="Plain", mana_value=6, oracle_text="Draw two cards.")
        assert engine.effective_cost(act) == 3
        assert engine.effective_cost(cruise) == 3
        assert engine.effective_cost(plain) == 6
