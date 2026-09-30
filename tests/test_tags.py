"""The tag-matching layer: boundary matching, anti-tags, and tribes.

Substring matching once counted `trample` as ramp, `hate-typal-human` as typal
support, and matched the bare tags `typal`/`copy`/`draw`/`removal` against
nothing at all — which is how two Oozes in an Ooze deck read as doing nothing.
"""

from __future__ import annotations

from mtgai import tags
from mtgai.model import CardEntry


class TestBoundaryMatching:
    def test_bare_tags_map_to_their_category(self):
        assert tags.categories_for("typal") == ["typal"]
        assert "copy" in tags.categories_for("copy")
        assert "draw" in tags.categories_for("draw")
        assert "removal" in tags.categories_for("removal")

    def test_prefixed_forms_still_match(self):
        assert "typal" in tags.categories_for("typal-ooze")
        assert "draw" in tags.categories_for("repeatable-draw")
        assert "draw" in tags.categories_for("pure-draw")
        assert "removal" in tags.categories_for("removal-creature")

    def test_a_pattern_no_longer_matches_mid_word(self):
        assert tags.categories_for("trample") == []
        assert tags.categories_for("reward") == []
        assert tags.categories_for("cyclone") == []

    def test_anti_tags_do_not_count_toward_what_they_punish(self):
        assert tags.categories_for("hate-typal-human") == []
        assert tags.categories_for("hate-typal-non-choose") == []

    def test_mass_removal_is_a_sweeper_not_spot_removal(self):
        assert tags.categories_for("mass-removal") == ["sweeper"]

    def test_hate_in_any_segment_is_anti(self):
        """`draw-hate` punishes drawing (Smothering Tithe); it does not draw."""
        assert tags.categories_for("draw-hate") == []
        assert tags.categories_for("single-minded-typal-hate") == []
        assert tags.categories_for("hate-lifegain") == []


class TestNewCategories:
    """The themes whose absence made engine pieces read as orphans."""

    def test_lifegain(self):
        for tag in ("lifegain", "repeatable-lifegain", "gives-lifelink", "lifegain-matters"):
            assert "lifegain" in tags.categories_for(tag), tag

    def test_synergy_allowlist_passes_the_engine_ones_only(self):
        assert "equipment" in tags.categories_for("synergy-equipment")
        assert "lifegain" in tags.categories_for("synergy-lifelink")
        assert tags.categories_for("synergy-white") == []
        assert tags.is_functional("synergy-equipment")

    def test_counters_cover_the_real_vocabulary(self):
        for tag in ("counter-increaser", "gains-pp-counters", "repeatable-pp-counters",
                    "pp-counters-matter", "counter-doubler"):
            assert "counters" in tags.categories_for(tag), tag

    def test_counters_skip_other_counter_kinds(self):
        for tag in ("counter-fuel", "counter-fuel-energy", "remove-counters",
                    "gives-mm-counters", "counters-matter", "counterspell"):
            assert "counters" not in tags.categories_for(tag), tag

    def test_pump_without_power_matters(self):
        assert "pump" in tags.categories_for("enlarge")
        assert "pump" in tags.categories_for("power-boost-to-all")
        assert tags.categories_for("power-matters") == []
        assert "pump" not in tags.categories_for("keyword-anthem")


class TestTagExclusions:
    def test_force_draw_and_draw_matters_are_not_draw(self):
        assert "draw" not in tags.categories_for("force-draw")
        assert "draw" not in tags.categories_for("draw-matters")
        assert "draw" in tags.categories_for("draw-engine")

    def test_graveyard_and_counterspell_sweepers_are_not_board_wipes(self):
        assert "sweeper" not in tags.categories_for("sweeper-graveyard")
        assert "sweeper" not in tags.categories_for("counterspell-sweeper")
        assert "sweeper" in tags.categories_for("sweeper-one-sided")

    def test_removing_equipment_is_not_running_equipment(self):
        assert "equipment" not in tags.categories_for("removal-equipment")


class TestCardLevelCorrections:
    def test_opponent_lifegain_cancels_the_bare_parent(self):
        """Swords to Plowshares gives the *opponent* life."""
        swords = CardEntry(name="Swords", tags=["lifegain", "opponent-lifegain", "removal"])
        assert "lifegain" not in tags.card_categories(swords)
        warden = CardEntry(name="Soul Warden", tags=["lifegain", "repeatable-lifegain"])
        assert "lifegain" in tags.card_categories(warden)

    def test_a_specific_lifegain_tag_survives_the_cancel(self):
        card = CardEntry(name="Both", tags=["lifegain", "opponent-lifegain", "gives-lifelink"])
        assert "lifegain" in tags.card_categories(card)

    def test_land_tutors_are_ramp_not_tutors(self):
        farseek = CardEntry(
            name="Farseek", tags=["tutor", "tutor-land", "tutor-to-battlefield", "ramp"]
        )
        assert tags.card_categories(farseek) == {"ramp"}
        demonic = CardEntry(name="Demonic Tutor", tags=["tutor", "tutor-to-hand"])
        assert "tutor" in tags.card_categories(demonic)

    def test_equipment_by_type_line(self):
        """Colossus Hammer carries no equipment tag; its type line says it."""
        hammer = CardEntry(name="Colossus Hammer", type_line="Artifact — Equipment", tags=["enlarge"])
        assert tags.card_categories(hammer) == {"equipment", "pump"}

    def test_removal_auras_are_not_voltron_pieces(self):
        pacifism = CardEntry(name="Pacifism", type_line="Enchantment — Aura", tags=["removal-aura"])
        assert "aura" not in tags.card_categories(pacifism)
        grace = CardEntry(name="Ethereal Armor", type_line="Enchantment — Aura", tags=["anthem"])
        assert "aura" in tags.card_categories(grace)


class TestTypalSubtypes:
    def test_extracts_the_tribe(self):
        card = CardEntry(name="Uugguu", tags=["typal-ooze", "typal", "typal-creature"])
        assert tags.typal_subtypes(card) == {"ooze"}

    def test_generic_typal_tags_are_not_tribes(self):
        card = CardEntry(name="Cavern", tags=["typal-creature", "typal-choose"])
        assert tags.typal_subtypes(card) == set()


class TestModelHelpers:
    def test_subtypes_come_from_the_front_face(self):
        card = CardEntry(name="X", type_line="Legendary Creature — Ooze Horror")
        assert card.subtypes == {"Ooze", "Horror"}
        mdfc = CardEntry(name="Y", type_line="Creature — Human // Land")
        assert "Land" not in mdfc.subtypes

    def test_changeling_by_keyword_or_tag(self):
        assert CardEntry(name="A", keywords=["Changeling"]).is_changeling
        assert CardEntry(name="B", tags=["changeling"]).is_changeling
        assert not CardEntry(name="C").is_changeling
