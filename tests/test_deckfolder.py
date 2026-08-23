"""Deck folders, round-tripping, and the notes.md guarantee."""

from __future__ import annotations

import json

from mtgai import deckfolder
from mtgai.analysis import report
from mtgai.model import CardEntry, Deck
from mtgai.sources import archidekt


def build(payload) -> Deck:
    return archidekt.normalise(payload, "test-deck-999001", enrich=False)


class TestSlugify:
    def test_slug_includes_the_deck_id(self):
        assert deckfolder.slugify("Current Edgar Deck", 7000000) == "current-edgar-deck-7000000"

    def test_punctuation_is_stripped(self):
        assert deckfolder.slugify("Atraxa's *Superfriends*!", 42) == "atraxa-s-superfriends-42"

    def test_unnamed_deck_falls_back_to_the_id(self):
        assert deckfolder.slugify("", 123) == "123"

    def test_long_names_are_truncated(self):
        slug = deckfolder.slugify("x" * 200, 7)
        assert slug.endswith("-7") and len(slug) <= 70


class TestRoundTrip:
    def test_deck_survives_serialisation(self, synthetic_payload):
        deck = build(synthetic_payload)
        restored = Deck.from_dict(json.loads(json.dumps(deck.to_dict())))

        assert restored.name == deck.name
        assert restored.total_cards == deck.total_cards
        assert restored.color_identity() == deck.color_identity()
        assert [c.name for c in restored.commanders] == [c.name for c in deck.commanders]

    def test_card_fields_survive(self, synthetic_payload):
        deck = build(synthetic_payload)
        restored = Deck.from_dict(json.loads(json.dumps(deck.to_dict())))
        original = deck.find("Any Colour Land")
        copy = restored.find("Any Colour Land")
        assert copy.mana_production == original.mana_production
        assert copy.oracle_id == original.oracle_id
        assert copy.scryfall_id == original.scryfall_id

    def test_unknown_keys_are_ignored(self):
        # A deck.json written by a future version must still load.
        card = CardEntry.from_dict({"name": "X", "quantity": 1, "invented_field": 1})
        assert card.name == "X"


class TestFolderWrites:
    def test_creates_the_expected_files(self, synthetic_payload):
        deck = build(synthetic_payload)
        folder = deckfolder.folder_for(deck.slug)
        folder.write_deck(deck)
        folder.write_analysis("# report")
        folder.write_suggestions("# suggestions")

        assert folder.deck_path.exists()
        assert (folder.path / deckfolder.DECKLIST).exists()
        assert folder.analysis_path.exists()
        assert folder.suggestions_path.exists()
        assert folder.notes_path.exists()

    def test_notes_are_never_overwritten(self, synthetic_payload):
        """notes.md is the user's file; a re-import must not touch it."""
        deck = build(synthetic_payload)
        folder = deckfolder.folder_for(deck.slug)
        folder.ensure()
        folder.notes_path.write_text("my irreplaceable thoughts")

        folder.write_deck(deck)
        folder.write_analysis("# regenerated")
        folder.write_suggestions("# regenerated")
        folder.ensure()

        assert folder.notes_path.read_text() == "my irreplaceable thoughts"

    def test_deck_reads_back(self, synthetic_payload):
        deck = build(synthetic_payload)
        folder = deckfolder.folder_for(deck.slug)
        folder.write_deck(deck)
        assert folder.read_deck().name == deck.name

    def test_resolve_finds_by_fragment(self, synthetic_payload):
        deck = build(synthetic_payload)
        deckfolder.folder_for(deck.slug).write_deck(deck)
        assert deckfolder.resolve("test-deck").slug == deck.slug
        assert deckfolder.resolve("999001").slug == deck.slug

    def test_resolve_raises_when_absent(self):
        try:
            deckfolder.resolve("nothing-here")
        except FileNotFoundError:
            return
        raise AssertionError("expected FileNotFoundError")


class TestDecklistRendering:
    def test_maybeboard_is_labelled_separately(self, synthetic_payload):
        deck = build(synthetic_payload)
        text = deckfolder.render_decklist(deck)
        assert "// Maybeboard" in text
        assert "Maybeboard Artifact" in text

        # Maybeboard cards must sit below the marker, never in the deck body.
        body, _, maybe = text.partition("// Maybeboard")
        assert "Maybeboard Artifact" not in body
        assert "Maybeboard Artifact" in maybe

    def test_commander_is_listed_once(self, synthetic_payload):
        deck = build(synthetic_payload)
        text = deckfolder.render_decklist(deck)
        assert text.count("Test Commander") == 1


class TestReportRendering:
    def test_analysis_renders_without_optional_sources(self, synthetic_payload):
        from mtgai import analysis

        deck = build(synthetic_payload)
        result = analysis.analyse(deck, offline=True)
        markdown = report.render_analysis(result)

        assert "# Synthetic Test Deck" in markdown
        assert "## Mana" in markdown
        assert "## Roles" in markdown
        # An illegal card has to reach the top of the report.
        assert "Off Colour Intruder" in markdown

    def test_suggestions_render_offline(self, synthetic_payload):
        from mtgai import analysis

        deck = build(synthetic_payload)
        result = analysis.analyse(deck, offline=True)
        markdown = report.render_suggestions(deck, result)

        assert "Consider adding" in markdown
        # The off-identity card is a required cut, not a suggestion.
        assert "Off Colour Intruder" in markdown
