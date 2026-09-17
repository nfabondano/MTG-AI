"""Analysis correctness — the numbers have to be right or the tool is noise."""

from __future__ import annotations

import pytest

from mtgai.analysis import bracket, curve, legality, mana, roles
from mtgai.model import CardEntry, Deck, color_pips
from mtgai.sources import archidekt


@pytest.fixture
def deck(synthetic_payload):
    d = archidekt.normalise(synthetic_payload, "test-deck", enrich=False)
    roles.tag_deck(d)
    return d


def make_deck(cards: list[CardEntry], **kwargs) -> Deck:
    d = Deck(slug="t", name="T", archidekt_id=1, cards=cards, **kwargs)
    roles.tag_deck(d)
    return d


class TestPipCounting:
    def test_counts_coloured_symbols(self):
        assert color_pips("{2}{W}{U}") == {"W": 1, "U": 1, "B": 0, "R": 0, "G": 0}

    def test_generic_mana_is_not_a_pip(self):
        assert sum(color_pips("{5}").values()) == 0

    def test_repeated_symbols_accumulate(self):
        assert color_pips("{B}{B}{B}")["B"] == 3

    def test_hybrid_counts_for_both_halves(self):
        # {W/U} is a demand for either colour, so it counts toward both.
        pips = color_pips("{W/U}")
        assert pips["W"] == 1 and pips["U"] == 1

    def test_phyrexian_counts_for_its_colour(self):
        assert color_pips("{B/P}")["B"] == 1

    def test_empty_cost_is_safe(self):
        assert sum(color_pips("").values()) == 0
        assert sum(color_pips(None).values()) == 0


class TestManaSources:
    """"Any colour" producers must be clipped to the deck's identity.

    Command Tower and friends report all five colours in the source data. Left
    unclipped, a Mardu deck reports phantom blue and green sources.
    """

    def test_any_colour_producer_is_clipped_to_identity(self, deck):
        # Identity is W/B from the commander. The deck holds three producers:
        # a land and an altar that each claim all five colours, and a modal
        # land that makes only B. Green and blue must not appear at all.
        result = mana.analyse(deck, avg_mv=3.0)
        by_colour = {c["color"]: c for c in result["colors"]}
        assert set(by_colour) == {"W", "B"}

        # White: the any-colour land and the any-colour altar.
        assert by_colour["W"]["total_sources"] == 2
        assert by_colour["W"]["land_sources"] == 1

        # Black: those two plus the modal land, which is black-only.
        assert by_colour["B"]["total_sources"] == 3
        assert by_colour["B"]["land_sources"] == 2

    def test_land_count_counts_quantity_not_entries(self):
        cards = [
            CardEntry(name="Swamp", quantity=10, type_line="Basic Land — Swamp",
                      mana_production={"B": 1}, color_identity=["B"]),
            CardEntry(name="Boss", quantity=1, type_line="Legendary Creature",
                      mana_cost="{B}", color_identity=["B"], is_commander=True),
        ]
        result = mana.analyse(make_deck(cards), avg_mv=2.0)
        assert result["land_count"] == 10

    def test_recommended_lands_rises_with_the_curve(self):
        cards = [CardEntry(name="Boss", type_line="Legendary Creature",
                           color_identity=["B"], is_commander=True)]
        cheap = mana.recommended_land_count(make_deck(cards), avg_mv=2.0)
        pricey = mana.recommended_land_count(make_deck(cards), avg_mv=4.5)
        assert cheap < pricey

    def test_recommended_lands_stays_in_a_sane_range(self):
        cards = [CardEntry(name="Boss", type_line="Legendary Creature", is_commander=True)]
        for avg in (0.5, 3.0, 9.0):
            assert 30 <= mana.recommended_land_count(make_deck(cards), avg_mv=avg) <= 42


class TestColourIdentity:
    def test_off_identity_card_is_an_error(self, deck):
        result = legality.analyse(deck)
        assert not result["legal"]
        assert any("Off Colour Intruder" in e for e in result["errors"])

    def test_identity_comes_from_the_commander(self, deck):
        # A green creature in the list must not widen the deck's identity.
        assert deck.color_identity() == ["W", "B"]

    def test_banned_card_is_reported(self, deck):
        result = legality.analyse(deck)
        assert any("Banned Spell" in e for e in result["errors"])

    def test_card_count_is_checked(self, deck):
        result = legality.analyse(deck)
        assert any("100" in e for e in result["errors"])

    def test_singleton_violation_is_caught(self):
        cards = [
            CardEntry(name="Boss", type_line="Legendary Creature", is_commander=True),
            CardEntry(name="Duplicate", quantity=2, type_line="Sorcery"),
        ]
        result = legality.analyse(make_deck(cards))
        assert any("Singleton" in e for e in result["errors"])

    def test_basic_lands_are_exempt_from_singleton(self):
        cards = [
            CardEntry(name="Boss", type_line="Legendary Creature", is_commander=True),
            CardEntry(name="Swamp", quantity=30, type_line="Basic Land — Swamp"),
        ]
        result = legality.analyse(make_deck(cards))
        assert not any("Singleton" in e for e in result["errors"])

    def test_non_commander_decks_skip_edh_rules(self):
        d = Deck(slug="t", name="T", archidekt_id=1, deck_format=1, format_name="Standard")
        result = legality.analyse(d)
        assert result["checked"] is False
        assert result["legal"] is True


class TestRoleClassification:
    def test_prose_mana_counts_as_ramp(self, deck):
        # "Add one mana of any colour" has no braces at all; a brace-only
        # pattern misses Phyrexian Altar and every card worded like it.
        assert "ramp" in deck.find("Prose Mana Altar").roles

    def test_edict_is_removal_not_a_board_wipe(self, deck):
        # "Each opponent sacrifices a creature" removes one creature per
        # player. Counting it as a wipe made sacrifice payoffs look like Wraths.
        card = deck.find("Test Edict")
        assert "removal" in card.roles
        assert "wipe" not in card.roles

    def test_destroy_all_is_a_board_wipe(self, deck):
        card = deck.find("Test Wrath")
        assert "wipe" in card.roles
        assert "removal" not in card.roles, "a wipe should not double-count as spot removal"

    def test_overload_reads_as_a_wipe(self):
        card = CardEntry(
            name="Damn",
            type_line="Sorcery",
            oracle_text="Destroy target creature.\nOverload {2}{W}{W}",
        )
        assert "wipe" in roles.classify(card)

    def test_lands_are_not_tagged_as_spells(self, deck):
        # A fetch land's "search your library" must not inflate the ramp count.
        modal = deck.find("Modal Front // Modal Back")
        assert modal.roles == ["land"]

    def test_modal_spell_land_is_judged_on_its_front_face(self):
        """A spell // land MDFC is not ramp just because its back taps for mana.

        Reading both faces classified Malakir Rebirth — a protection instant —
        as ramp, because its land half says "{T}: Add {B}". You cast one side
        or the other, so only the front face describes the spell.
        """
        card = CardEntry(
            name="Malakir Rebirth // Malakir Mire",
            type_line="Instant",
            layout="modal_dfc",
            mana_production={"B": 1},
            oracle_text=(
                "Choose target creature. You lose 2 life. Until end of turn, that "
                'creature gains "When this creature dies, return it to the battlefield."'
                "\n//\n"
                "This land enters tapped.\n{T}: Add {B}."
            ),
        )
        assert "ramp" not in roles.classify(card)

    def test_transform_cards_still_read_both_faces(self):
        # A transforming permanent is one object, so both halves count.
        card = CardEntry(
            name="Front // Back",
            type_line="Creature",
            layout="transform",
            oracle_text="Vanilla.\n//\nDestroy all creatures.",
        )
        assert "wipe" in roles.classify(card)

    def test_overrides_win_over_patterns(self):
        card = CardEntry(name="Sol Ring", type_line="Artifact", oracle_text="{T}: Add {C}{C}.")
        assert roles.classify(card) == ["ramp"]

    def test_counts_are_weighted_by_quantity(self):
        cards = [
            CardEntry(name="Sol Ring", quantity=3, type_line="Artifact",
                      oracle_text="{T}: Add {C}{C}."),
        ]
        assert roles.counts(make_deck(cards))["ramp"] == 3

    def test_findings_flag_a_shortfall(self, deck):
        result = roles.analyse(deck)
        low = {f["role"] for f in result["findings"] if f["verdict"] == "low"}
        assert "ramp" in low


class TestCurve:
    def test_lands_are_excluded_from_the_curve(self, deck):
        result = curve.analyse(deck)
        # The synthetic deck has 2 lands; only the 6 nonland cards curve.
        assert result["nonland_count"] == len(deck.nonland)
        assert result["nonland_count"] == 6

    def test_seven_plus_is_one_bucket(self):
        cards = [CardEntry(name="Big", mana_value=11, type_line="Sorcery")]
        result = curve.analyse(make_deck(cards))
        assert result["histogram"]["7"] == 1

    def test_average_is_weighted_by_quantity(self):
        cards = [
            CardEntry(name="A", quantity=3, mana_value=2, type_line="Sorcery"),
            CardEntry(name="B", quantity=1, mana_value=6, type_line="Sorcery"),
        ]
        result = curve.analyse(make_deck(cards))
        assert result["average_mana_value"] == 3.0

    def test_empty_deck_does_not_divide_by_zero(self):
        result = curve.analyse(make_deck([]))
        assert result["average_mana_value"] == 0.0


class TestBracket:
    def test_game_changers_raise_the_bracket(self):
        cards = [
            CardEntry(name=f"GC{i}", type_line="Artifact", is_game_changer=True)
            for i in range(5)
        ]
        result = bracket.analyse(make_deck(cards))
        assert result["estimate"] == 4

    def test_a_few_game_changers_land_in_bracket_three(self):
        cards = [
            CardEntry(name="GC", type_line="Artifact", is_game_changer=True),
            CardEntry(name="Filler", type_line="Sorcery"),
        ]
        assert bracket.analyse(make_deck(cards))["estimate"] == 3

    def test_mass_land_denial_forces_bracket_four(self):
        cards = [CardEntry(name="Armageddon", type_line="Sorcery", is_mass_land_denial=True)]
        assert bracket.analyse(make_deck(cards))["estimate"] == 4

    def test_a_plain_deck_stays_low(self):
        cards = [CardEntry(name="Bear", type_line="Creature", oracle_text="Vanilla.")]
        assert bracket.analyse(make_deck(cards))["estimate"] <= 2

    def test_mismatch_with_archidekt_is_reported(self):
        cards = [
            CardEntry(name=f"GC{i}", type_line="Artifact", is_game_changer=True)
            for i in range(5)
        ]
        d = make_deck(cards, archidekt_bracket=2)
        assert bracket.analyse(d)["mismatch"] is not None

    def test_one_extra_turn_is_still_a_core_deck(self):
        # Brackets 1-3 forbid *chaining* extra turns; a single Time Warp is fine.
        cards = [
            CardEntry(name="Time Warp", type_line="Sorcery", is_extra_turns=True),
            CardEntry(name="Wrath", type_line="Sorcery", oracle_text="Destroy all creatures."),
        ]
        assert bracket.analyse(make_deck(cards))["estimate"] == 2

    def test_two_extra_turns_is_chaining(self):
        cards = [
            CardEntry(name=f"Warp{i}", type_line="Sorcery", is_extra_turns=True)
            for i in range(2)
        ]
        assert bracket.analyse(make_deck(cards))["estimate"] == 4

    def test_maybeboard_game_changers_are_flagged_not_counted(self):
        cards = [CardEntry(name="Wrath", type_line="Sorcery", oracle_text="Destroy all creatures.")]
        d = make_deck(cards, archidekt_bracket=2)
        d.excluded = [
            {"name": f"GC{i}", "categories": ["Maybeboard"], "is_game_changer": True}
            for i in range(4)
        ] + [{"name": "Armageddon", "categories": ["Maybeboard"], "is_mass_land_denial": True}]
        result = bracket.analyse(d)
        assert result["estimate"] == 2, "the maybeboard must not move the estimate"
        maybe = result["maybeboard"]
        assert len(maybe["game_changers"]) == 4
        assert any("bracket 3" in w for w in maybe["warnings"])
        assert any("bracket 4" in w and "more than 3" in w for w in maybe["warnings"])
        assert any("Armageddon" in w for w in maybe["warnings"])

    def test_empty_maybeboard_has_no_warnings(self):
        cards = [CardEntry(name="Bear", type_line="Creature")]
        assert bracket.analyse(make_deck(cards))["maybeboard"]["warnings"] == []


class TestComboBracket:
    def test_casual_combos_do_not_raise_the_bracket(self):
        cards = [CardEntry(name="Wrath", type_line="Sorcery", oracle_text="Destroy all creatures.")]
        casual = {"cards": ["Orthion, Hero of Lavabrink", "Terror of the Peaks"], "bracket_tag": "C"}
        result = bracket.analyse(make_deck(cards), combos=[casual])
        assert result["estimate"] == 2
        assert result["casual_combos"] == [casual["cards"]]
        assert any("not counted" in r for r in result["reasons"])

    def test_rated_or_unrated_combos_raise_to_three(self):
        cards = [CardEntry(name="Wrath", type_line="Sorcery", oracle_text="Destroy all creatures.")]
        for tag in ("R", "S", "P", "O", ""):
            combo = {"cards": ["A", "B"], "bracket_tag": tag}
            result = bracket.analyse(make_deck(cards), combos=[combo])
            assert result["estimate"] == 3, tag
            assert result["combos"] == [["A", "B"]]


class TestComboProbeOrder:
    def test_archidekt_combo_flags_beat_edhrec_rank(self):
        from mtgai.analysis import combos

        cards = [
            CardEntry(name="Commander", type_line="Creature", is_commander=True),
            CardEntry(name="Popular Filler", type_line="Artifact", edhrec_rank=10),
            CardEntry(name="Obscure Combo Piece", type_line="Creature", edhrec_rank=9000, combo_flagged=True),
            CardEntry(name="Swamp", type_line="Basic Land — Swamp"),
        ]
        order = combos._probe_order(make_deck(cards), 10)
        assert order == ["Commander", "Obscure Combo Piece", "Popular Filler"]
