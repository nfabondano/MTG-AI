"""Archidekt parsing — the two traps that silently corrupt a deck."""

from __future__ import annotations

import pytest

from mtgai.sources import archidekt


def normalise(payload):
    """Normalise without enrichment; enrichment needs Scryfall."""
    return archidekt.normalise(payload, "test-deck", enrich=False)


class TestDeckIdParsing:
    @pytest.mark.parametrize(
        "reference,expected",
        [
            ("7000000", 7000000),
            ("https://archidekt.com/decks/7000000", 7000000),
            ("https://archidekt.com/decks/7000000/current-edgar-deck", 7000000),
            ("archidekt.com/decks/123/x", 123),
            ("https://archidekt.com/api/decks/456/", 456),
        ],
    )
    def test_accepts_urls_and_ids(self, reference, expected):
        assert archidekt.parse_deck_id(reference) == expected

    def test_rejects_garbage(self):
        with pytest.raises(ValueError):
            archidekt.parse_deck_id("not-a-deck")

    def test_prefers_the_deck_id_over_other_numbers(self):
        # A slug containing digits must not win over the real id.
        assert archidekt.parse_deck_id("https://archidekt.com/decks/321/deck-2024") == 321


class TestMaybeboardExclusion:
    """A maybeboard card carries BOTH `Maybeboard` and its type category.

    Excluding only cards whose categories are *all* flagged out would let every
    maybeboard card into the deck, silently inflating the count and poisoning
    every downstream number.
    """

    def test_excludes_card_flagged_by_any_category(self, synthetic_payload):
        deck = normalise(synthetic_payload)
        names = {c.name for c in deck.cards}
        assert "Maybeboard Artifact" not in names
        assert "Sideboard Card" not in names

    def test_excluded_cards_are_recorded_not_dropped(self, synthetic_payload):
        deck = normalise(synthetic_payload)
        excluded = {e["name"] for e in deck.excluded}
        assert excluded == {"Maybeboard Artifact", "Sideboard Card"}

    def test_keeps_cards_sharing_a_type_category_with_a_maybeboard_card(
        self, synthetic_payload
    ):
        # "Test Wrath" is a Sorcery, as is the excluded "Sideboard Card"; the
        # shared type category must not drag it out of the deck.
        deck = normalise(synthetic_payload)
        assert "Test Wrath" in {c.name for c in deck.cards}

    def test_excluded_cards_keep_their_bracket_flags(self, synthetic_payload):
        # The bracket estimate needs to know what the maybeboard would do.
        deck = normalise(synthetic_payload)
        entry = next(e for e in deck.excluded if e["name"] == "Maybeboard Artifact")
        for key in ("is_game_changer", "is_tutor", "is_extra_turns", "is_mass_land_denial"):
            assert entry[key] is False
        assert entry["color_identity"] == []

    def test_real_deck_with_maybeboard(self, maybeboard_payload):
        deck = normalise(maybeboard_payload)
        assert len(deck.excluded) == 7
        for entry in deck.excluded:
            assert "Maybeboard" in entry["categories"]
        assert not any("Maybeboard" in c.categories for c in deck.cards)


class TestOracleAndPrintingIds:
    """`card.uid` is the printing id; `card.oracleCard.uid` is the oracle id."""

    def test_ids_come_from_the_right_fields(self, synthetic_payload):
        deck = normalise(synthetic_payload)
        commander = deck.find("Test Commander")
        assert commander.scryfall_id == "printing-id-commander"
        assert commander.oracle_id == "oracle-id-commander"

    def test_the_two_ids_are_never_conflated(self, synthetic_payload):
        deck = normalise(synthetic_payload)
        for card in deck.cards:
            assert card.scryfall_id != card.oracle_id


class TestModalDoubleFacedCards:
    """Modal DFCs carry an empty top-level `text`; the rules text is on faces."""

    def test_face_text_is_recovered(self, synthetic_payload):
        deck = normalise(synthetic_payload)
        modal = deck.find("Modal Front // Modal Back")
        assert modal is not None
        assert "Add {B}" in modal.oracle_text
        assert "Search your library" in modal.oracle_text

    def test_real_modal_cards_have_text(self, maybeboard_payload):
        deck = normalise(maybeboard_payload)
        modal = [c for c in deck.cards + [] if "//" in c.name]
        for card in modal:
            assert card.oracle_text, f"{card.name} lost its rules text"


class TestNormalisation:
    def test_type_line_is_rebuilt_from_split_fields(self, synthetic_payload):
        deck = normalise(synthetic_payload)
        commander = deck.find("Test Commander")
        assert commander.type_line == "Legendary Creature — Vampire"

    def test_colours_are_mapped_to_scryfall_letters(self, synthetic_payload):
        deck = normalise(synthetic_payload)
        commander = deck.find("Test Commander")
        assert commander.colors == ["W", "B"]
        assert commander.color_identity == ["W", "B"]

    def test_mana_production_keeps_only_produced_colours(self, synthetic_payload):
        deck = normalise(synthetic_payload)
        land = deck.find("Any Colour Land")
        assert land.mana_production == {"W": 1, "U": 1, "B": 1, "R": 1, "G": 1}
        commander = deck.find("Test Commander")
        assert commander.mana_production == {}

    def test_commander_is_identified(self, synthetic_payload):
        deck = normalise(synthetic_payload)
        assert [c.name for c in deck.commanders] == ["Test Commander"]

    def test_format_is_named(self, synthetic_payload):
        deck = normalise(synthetic_payload)
        assert deck.deck_format == 3
        assert deck.format_name == "Commander"
        assert deck.is_commander

    def test_banned_card_legality_is_carried_through(self, synthetic_payload):
        deck = normalise(synthetic_payload)
        assert deck.find("Banned Spell").commander_legal is False
        assert deck.find("Test Wrath").commander_legal is True
