"""Advice the tool gave that had to be thrown away by hand — never again.

Each class is one row of that list, pinned against the real deck it happened
on (trimmed Archidekt payloads in tests/fixtures). If one of these fails, the
tool is about to repeat a mistake it already made in front of Nicolas.
"""

from __future__ import annotations

from mtgai import analysis, tags
from mtgai.analysis import engine, suggest
from mtgai.model import CardEntry, Deck


def _engine(deck: Deck, intent=None) -> dict:
    tags.ensure_tags(deck)
    return engine.analyse(deck, intent)


def _cuts_and_orphans(deck: Deck) -> tuple[set[str], set[str], dict]:
    result = analysis.analyse(deck, offline=True)
    cuts = {c["name"] for c in suggest.build_cuts(deck, result)}
    return cuts, set(result["engine"]["orphans"]), result


# The Niv-Mizzet cards the old report offered as "no engine participation" or
# "oversupplied: removal" — the lifegain engine and a win condition.
NIV_ENGINE = [
    "Soul Warden",
    "Soul's Attendant",
    "Authority of the Consuls",
    "Children of Korlis",
    "Serra Ascendant",
    "Aetherflux Reservoir",
]

# Cloud's "outside every cluster" list: the Equipment that is the deck.
CLOUD_EQUIPMENT = [
    "Colossus Hammer",
    "Excalibur, Sword of Eden",
    "Legion Leadership // Legion Stronghold",
    "Aettir and Priwen",
    "Conformer Shuriken",
    "Wrecking Ball Arm",
    "Long-Lost Lances",
]


class TestNivReadsAsLifegain:
    """Niv-Mizzet, Ghost Counsel draws off every lifegain event. The tool had
    no lifegain category, so it read the deck as drain + tokens and offered
    Soul Warden and friends as cuts."""

    def test_lifegain_is_what_the_commander_asks_for(self, niv):
        result = _engine(niv)
        assert "lifegain" in result["commander_wants"]
        assert result["archetype"].startswith("lifegain")

    def test_the_99_is_asked_for_lifegain_not_sacrifice_outlets(self, niv):
        role = engine.classify_commander(niv)
        assert "payoff" in role["roles"]
        text = " ".join(role["supplies"])
        assert "lifegain" in text
        assert "sacrifice outlets" not in text

    def test_the_lifegain_engine_is_never_a_cut(self, niv):
        cuts, orphans, _ = _cuts_and_orphans(niv)
        offered = [n for n in NIV_ENGINE if n in cuts or n in orphans]
        assert not offered, f"offered the lifegain engine as cuts: {offered}"


class TestCloudReadsAsEquipment:
    """Cloud, Ex-SOLDIER read as "glue" with a Human tribe, so every
    Equipment was an orphan and every Human was exempt from cuts."""

    def test_equipment_is_what_the_commander_asks_for(self, equipments):
        wants = set(_engine(equipments)["commander_wants"])
        assert {"equipment", "pump", "protection"} <= wants
        # Treasure tokens are ramp, not a sacrifice theme.
        assert "sacrifice" not in wants and "tokens" not in wants

    def test_cloud_is_a_payoff_by_name(self, equipments):
        """ "Whenever Cloud attacks" names the commander; the role patterns
        used to recognise only "whenever a/another/you"."""
        role = engine.classify_commander(equipments)
        assert "payoff" in role["roles"]
        assert any("Equipment" in s for s in role["supplies"])

    def test_the_commanders_own_type_is_not_a_tribe(self, equipments):
        assert engine.commander_tribe(equipments) is None

    def test_the_equipment_is_never_a_cut(self, equipments):
        cuts, orphans, _ = _cuts_and_orphans(equipments)
        offered = [n for n in CLOUD_EQUIPMENT if n in cuts or n in orphans]
        assert not offered, f"offered the Equipment as cuts: {offered}"

    def test_three_colours_with_plenty_of_sources_is_not_a_cut(self, equipments):
        """Tifa {1}{R}{G}{W} in a deck with 26/24/22 sources."""
        _, _, result = _cuts_and_orphans(equipments)
        strain = {f["name"]: f for f in result["engine"]["castability"]}
        assert "Tifa, Martial Artist" in strain, "still worth mentioning as info"
        assert not strain["Tifa, Martial Artist"]["three_colour_strain"]
        cut_names = {f["name"] for f in result["engine"]["castability_cuts"]}
        assert "Tifa, Martial Artist" not in cut_names

    def test_equipment_tutors_are_not_spare_tutors(self, equipments):
        """Stoneforge Mystic and friends serve the Equipment engine."""
        _, _, result = _cuts_and_orphans(equipments)
        over = {e["category"]: e for e in result["engine"]["oversupplied"]}
        if "tutor" in over:
            assert over["tutor"]["cuttable"] == 0


class TestFelisaReadsAsCounters:
    def test_no_vampire_tribe_from_her_type_line(self, felisa):
        assert engine.commander_tribe(felisa) is None

    def test_supplies_cover_deaths_and_counters(self, felisa):
        role = engine.classify_commander(felisa)
        text = " ".join(role["supplies"])
        assert "sacrifice outlets" in text
        assert "+1/+1 counters" in text

    def test_laezel_multiplies_counters(self, felisa):
        laezel = felisa.find("Lae'zel, Vlaakith's Champion")
        assert "counters" in tags.card_categories(laezel)

    def test_support_cards_are_not_orphans(self, felisa):
        """Demonic Tutor has a job even though three tutors form no cluster."""
        cuts, orphans, _ = _cuts_and_orphans(felisa)
        for name in ("Demonic Tutor", "Lae'zel, Vlaakith's Champion", "Teferi's Protection"):
            assert name not in cuts, f"{name} offered as a cut"
            assert name not in orphans, f"{name} called an orphan"

    def test_protection_padded_by_engine_pieces_is_not_oversupplied(self, felisa):
        """Nine protection cards, but seven are engine pieces (Broodmoth,
        Valkyrie's Call, Yawgmoth…) that also protect."""
        _, _, result = _cuts_and_orphans(felisa)
        over = {e["category"]: e for e in result["engine"]["oversupplied"]}
        assert over["protection"]["dedicated"] == 2
        assert over["protection"]["cuttable"] == 0


class TestFreeSpellsAreNotExpensive:
    """Deadly Rollick and Flawless Maneuver cost nothing with the commander
    out; the old ranking picked them first for their printed mana value."""

    def test_effective_cost_is_zero(self, niv):
        for name in ("Deadly Rollick", "Flawless Maneuver"):
            assert engine.effective_cost(niv.find(name)) == 0

    def test_never_the_representative(self, niv):
        cuts = suggest.build_cuts(niv, analysis.analyse(niv, offline=True))
        assert not {c["name"] for c in cuts} & {"Deadly Rollick", "Flawless Maneuver"}

    def test_ranking_puts_them_last(self):
        free = CardEntry(
            name="Free Spell", mana_value=4, tags=["removal"],
            oracle_text="If you control a commander, you may cast this spell without "
            "paying its mana cost.\nExile target creature.",
        )
        cheap = CardEntry(name="Cheap Spell", mana_value=1, tags=["removal"])
        costly = CardEntry(name="Costly Spell", mana_value=5, tags=["removal"])
        ranked = engine.rank_cut_candidates([free, cheap, costly])
        assert [c.name for c in ranked] == ["Costly Spell", "Cheap Spell", "Free Spell"]

    def test_flexible_cards_lead_and_game_changers_trail(self):
        gc = CardEntry(name="Changer", mana_value=6, tags=["ramp"], is_game_changer=True)
        plain = CardEntry(name="Plain", mana_value=2, tags=["ramp"])
        flex = CardEntry(name="Flex", mana_value=1, tags=["ramp"])
        ranked = engine.rank_cut_candidates([gc, plain, flex], flexible={"flex"})
        assert [c.name for c in ranked] == ["Flex", "Plain", "Changer"]

    def test_measured_inclusion_only_breaks_ties(self):
        a = CardEntry(name="Alpha", mana_value=3, tags=["ramp"])
        b = CardEntry(name="Beta", mana_value=3, tags=["ramp"])
        ranked = engine.rank_cut_candidates([a, b], inclusion={"beta": 0.05, "alpha": 0.60})
        assert [c.name for c in ranked] == ["Beta", "Alpha"]
        # Unmeasured is treated as widely played: absence is not evidence.
        ranked = engine.rank_cut_candidates([a, b], inclusion={"alpha": 0.60})
        assert [c.name for c in ranked] == ["Alpha", "Beta"]


class TestFelisaCombos:
    """The report listed fifteen "two-card combos"; Spellbook finds four, all
    three-card, and none of them moves the bracket."""

    def test_no_two_card_combo_reason(self, monkeypatch, felisa, spellbook_fmc):
        from mtgai.analysis import bracket, combos
        from mtgai.sources import spellbook

        monkeypatch.setattr(spellbook, "post_json", lambda url, body, **k: spellbook_fmc["felisa"])
        found = combos.analyse(felisa)
        result = bracket.analyse(felisa, combos=found["complete"])
        assert result["combos"] == []
        assert not any("two-card combo" in r and "in the deck:" in r for r in result["reasons"])
        assert any("three or more pieces" in r for r in result["reasons"])
        # Three Game Changers still make it bracket 3 — for the right reason.
        assert result["estimate"] == 3
        assert any("3 Game Changers" in r for r in result["reasons"])


class TestRoleCountsAreHonest:
    def test_minus_x_wipes_are_wipes(self):
        from mtgai.analysis import roles

        deluge = CardEntry(
            name="Toxic Deluge", type_line="Sorcery",
            oracle_text="As an additional cost to cast this spell, pay X life.\n"
            "All creatures get -X/-X until end of turn.",
        )
        one_sided = CardEntry(
            name="Crippling Fear", type_line="Sorcery",
            oracle_text="Choose a creature type. Creatures your opponents control get -3/-3 "
            "until end of turn.",
        )
        assert roles.classify(deluge) == ["wipe"]
        assert "wipe" in roles.classify(one_sided)

    def test_niv_is_not_told_it_has_no_wipes(self, niv):
        """Toxic Deluge is in the deck; the report said "0 wipe"."""
        result = analysis.analyse(niv, offline=True)
        assert result["roles"]["counts"]["wipe"] >= 1
        assert result["roles"]["tag_counts"]["wipe"] >= 1
        assert not any(f["message"].startswith("0 wipe") for f in result["roles"]["findings"])

    def test_draw_is_not_padded_by_punishers(self, niv):
        """Smothering Tithe punishes draws and Dawn's Truce gifts one."""
        for name in ("Smothering Tithe", "Dawn's Truce"):
            assert "draw" not in tags.card_categories(niv.find(name)), name


class TestReminderTextIsNotDesign:
    def test_mentor_reminder_does_not_make_a_payoff(self):
        card = CardEntry(
            name="Mentor Guy",
            type_line="Legendary Creature — Human",
            oracle_text="Mentor (Whenever this creature attacks, put a +1/+1 counter "
            "on target attacking creature with lesser power.)",
            is_commander=True,
        )
        deck = Deck(slug="t", name="T", archidekt_id=1, cards=[card])
        assert "payoff" not in engine.classify_commander(deck)["roles"]


class TestFreeSpellsAreNotAlternatives:
    """After the representative stopped being Flawless Maneuver, it came back
    as "or instead: …, Flawless Maneuver" — the same advice, one line down."""

    def test_not_offered_as_an_alternative_either(self, niv, equipments):
        for deck in (niv, equipments):
            cuts = suggest.build_cuts(deck, analysis.analyse(deck, offline=True))
            for cut in cuts:
                offered = [cut["name"], *(cut.get("alternatives") or [])]
                assert not {"Flawless Maneuver", "Deadly Rollick"} & set(offered), cut


class TestTutorsInAComboDeck:
    """Nine tutors are a surplus for a deck that wins on board, not for one
    assembling combos — Raggadraga was told to cut Chord of Calling."""

    def test_complete_combos_keep_the_tutors(self):
        from mtgai.analysis import _tutors_find_combos

        engine_result = {"oversupplied": [
            {"category": "tutor", "count": 9, "cuttable": 5},
            {"category": "draw", "count": 18, "cuttable": 4},
        ]}
        _tutors_find_combos(engine_result, {"complete": [{"cards": ["A", "B"]}]})
        tutor, draw = engine_result["oversupplied"]
        assert tutor["cuttable"] == 0 and "finds the pieces" in tutor["note"]
        assert draw["cuttable"] == 4

    def test_without_combos_the_surplus_stands(self):
        from mtgai.analysis import _tutors_find_combos

        engine_result = {"oversupplied": [{"category": "tutor", "count": 9, "cuttable": 5}]}
        _tutors_find_combos(engine_result, {"complete": []})
        assert engine_result["oversupplied"][0]["cuttable"] == 5


class TestCurveCutsSpareTheEngine:
    def test_curve_cuts_are_top_end_and_engine_free_first(self, equipments):
        """The curve tier used to take the two priciest cards, engine or not."""
        result = analysis.analyse(equipments, offline=True)
        result["curve"]["expensive_spells"] = 20  # force the top-heavy branch
        cuts = [c for c in suggest.build_cuts(equipments, result) if c["evidence"] == "curve"]
        core = set(result["engine"]["core"])
        assert 0 < len(cuts) <= 2
        for cut in cuts:
            assert engine.effective_cost(equipments.find(cut["name"])) >= 5
        engine_free = {
            c.name for c in equipments.cards
            if not c.is_land and not c.is_commander and engine.effective_cost(c) >= 5
            and not set(c.engine_participation) & core and not engine.last_resort(c)
        }
        # Austere Command is the only card outside the equipment engine at the
        # top of the curve — and one of the deck's two sweepers, so it stays.
        assert engine_free == {"Austere Command"}
        assert "Austere Command" not in {c["name"] for c in cuts}
