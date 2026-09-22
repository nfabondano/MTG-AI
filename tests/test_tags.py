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
