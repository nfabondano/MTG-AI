"""Decks that move on Archidekt: renamed, or rebuilt under a new link.

Niv-Mizzet was rebuilt at 26941717 and the old link started answering 404; the
refresh failed, and the folder had to be moved by hand with its intent.md.
A rename on Archidekt quietly produced a second folder. Neither again.
"""

from __future__ import annotations

import copy

import pytest

from conftest import REAL_DECKS, load_fixture
from mtgai import deckfolder, service
from mtgai.http import NotFound
from mtgai.intent import DeckIntent
from mtgai.sources import archidekt

OLD_ID = 26909132
NEW_ID = 26941717
NOTES = "# Notes\n\nSheoldred stays. Try Starscape Cleric.\n"


@pytest.fixture
def niv_payloads():
    new = load_fixture(REAL_DECKS["niv"][0])
    old = copy.deepcopy(new)
    old["id"] = OLD_ID
    return {OLD_ID: old, NEW_ID: new}


@pytest.fixture
def archidekt_world(monkeypatch, niv_payloads):
    """Archidekt as it stood: the old link alive until `gone` is set."""
    state = {"gone": set(), "search": load_fixture("archidekt_search_niv.json"), "searches": []}

    def fetch_raw(deck_id, use_cache=True):
        if deck_id in state["gone"] or deck_id not in niv_payloads:
            raise NotFound(f"not found: {deck_id}", status=404)
        return niv_payloads[deck_id]

    def get_json(url, params=None, use_cache=True, **kwargs):
        assert url == archidekt.SEARCH_API, url
        assert use_cache is False
        state["searches"].append(dict(params or {}))
        return state["search"]

    monkeypatch.setattr(archidekt, "fetch_raw", fetch_raw)
    monkeypatch.setattr(archidekt, "get_json", get_json)
    return state


@pytest.fixture
def old_folder(archidekt_world):
    """The old Niv folder, with Nicolas's notes, intent and engine edits."""
    added = service.add_deck(str(OLD_ID), offline=True)
    folder = deckfolder.folder_for(added["slug"])
    folder.notes_path.write_text(NOTES)
    folder.write_intent(DeckIntent(archetype="lifegain combo", power_bracket=4))
    folder.engine_path.write_text("# Engine\n\nMy own reading: lifegain into cards.\n")
    archidekt_world["gone"].add(OLD_ID)
    return folder


class TestRelink:
    def test_carries_notes_intent_and_edited_engine_then_removes_the_old(self, old_folder):
        result = service.relink_deck(
            old_folder.slug, f"https://archidekt.com/decks/{NEW_ID}/niv", offline=True
        )
        new = deckfolder.folder_for(result["slug"])
        assert new.slug.endswith(str(NEW_ID))
        assert new.notes_path.read_text() == NOTES
        assert new.read_intent()[0].power_bracket == 4
        assert "My own reading" in new.engine_path.read_text()
        assert not old_folder.path.exists()
        assert result["moved"]["removed"]
        assert result["relinked"] == {"from": OLD_ID, "to": NEW_ID, "followed": False}

    def test_the_intent_speaks_in_the_first_analysis(self, old_folder):
        result = service.relink_deck(old_folder.slug, str(NEW_ID), offline=True)
        assert result["analysis"]["intent"]["power_bracket"] == 4

    def test_template_notes_are_not_carried(self, archidekt_world):
        added = service.add_deck(str(OLD_ID), offline=True)
        archidekt_world["gone"].add(OLD_ID)
        result = service.relink_deck(added["slug"], str(NEW_ID), offline=True)
        assert "notes.md" in result["moved"]["skipped"]
        new = deckfolder.folder_for(result["slug"])
        assert "Carried over" not in new.notes_path.read_text()

    def test_notes_already_in_the_new_folder_are_appended_to(self, old_folder):
        new = deckfolder.folder_for(deckfolder.slugify("Niv-Mizzet", NEW_ID))
        new.ensure()
        new.notes_path.write_text("# Notes\n\nNew list, new ideas.\n")
        service.relink_deck(old_folder.slug, str(NEW_ID), offline=True)
        text = new.notes_path.read_text()
        assert text.startswith("# Notes\n\nNew list, new ideas.")
        assert f"## Carried over from {old_folder.slug}" in text
        assert "Sheoldred stays." in text

    def test_two_different_intents_are_both_kept(self, old_folder):
        new = deckfolder.folder_for(deckfolder.slugify("Niv-Mizzet", NEW_ID))
        new.write_intent(DeckIntent(archetype="drain", power_bracket=3))
        result = service.relink_deck(old_folder.slug, str(NEW_ID), offline=True)
        assert result["moved"]["conflicts"] == ["intent.md"]
        assert not result["moved"]["removed"]
        assert old_folder.path.exists()
        assert new.read_intent()[0].power_bracket == 3


class TestRefreshOfAMovedDeck:
    def test_a_404_says_where_it_went_and_changes_nothing(self, old_folder, archidekt_world):
        before = sorted(p.name for p in deckfolder.decks_dir().iterdir())
        with pytest.raises(service.DeckMoved) as caught:
            service.refresh_deck(old_folder.slug, offline=True)
        moved = caught.value
        assert [c["id"] for c in moved.candidates] == [NEW_ID, 10078100]
        assert moved.best["id"] == NEW_ID
        assert f"mtg deck refresh {old_folder.slug} --follow" in moved.commands
        assert "404" in str(moved)
        assert moved.to_dict()["status"] == "moved"
        assert sorted(p.name for p in deckfolder.decks_dir().iterdir()) == before
        assert archidekt_world["searches"][0] == {"ownerUsername": "Hugo0111", "name": "Niv-Mizzet"}

    def test_follow_links_the_one_deck_with_the_same_name(self, old_folder):
        result = service.refresh_deck(old_folder.slug, offline=True, follow=True)
        assert result["relinked"] == {"from": OLD_ID, "to": NEW_ID, "followed": True}
        assert not old_folder.path.exists()
        assert deckfolder.folder_for(result["slug"]).notes_path.read_text() == NOTES

    def test_follow_does_nothing_when_two_decks_could_be_it(self, old_folder, archidekt_world):
        search = copy.deepcopy(archidekt_world["search"])
        search["results"][0]["name"] = "Niv-Mizzet v2"
        archidekt_world["search"] = search
        with pytest.raises(service.DeckMoved) as caught:
            service.refresh_deck(old_folder.slug, offline=True, follow=True)
        assert caught.value.best is None
        assert old_folder.path.exists()
        assert not deckfolder.folder_for(deckfolder.slugify("Niv-Mizzet", NEW_ID)).path.exists()

    def test_a_rename_on_archidekt_leaves_one_folder(self, archidekt_world, niv_payloads):
        added = service.add_deck(str(NEW_ID), offline=True)
        folder = deckfolder.folder_for(added["slug"])
        folder.notes_path.write_text(NOTES)
        niv_payloads[NEW_ID] = dict(niv_payloads[NEW_ID], name="Niv Lifegain")
        result = service.refresh_deck(added["slug"], offline=True)
        assert result["slug"] == deckfolder.slugify("Niv Lifegain", NEW_ID)
        assert not folder.path.exists()
        assert [f.slug for f in deckfolder.all_folders()] == [result["slug"]]
        assert deckfolder.folder_for(result["slug"]).notes_path.read_text() == NOTES

    def test_adding_a_renamed_deck_again_is_the_same_deck(self, archidekt_world, niv_payloads):
        first = service.add_deck(str(NEW_ID), offline=True)
        niv_payloads[NEW_ID] = dict(niv_payloads[NEW_ID], name="Niv Lifegain")
        second = service.add_deck(str(NEW_ID), offline=True)
        assert second["moved"]["from"] == first["slug"]
        assert [f.slug for f in deckfolder.all_folders()] == [second["slug"]]


class TestSearch:
    def test_exact_owner_only_exact_name_first_then_newest(self, monkeypatch):
        response = {
            "next": None,
            "results": [
                {"id": 1, "name": "Niv old", "updatedAt": "2024-01-01", "owner": {"username": "Hugo0111"}},
                {"id": 2, "name": "Niv new", "updatedAt": "2026-01-01", "owner": {"username": "Hugo0111"}},
                {"id": 3, "name": "Niv", "updatedAt": "2023-01-01", "owner": {"username": "hugo0111"}},
                {"id": 4, "name": "Niv", "updatedAt": "2026-06-01", "owner": {"username": "Hugo01110"}},
            ],
        }
        monkeypatch.setattr(archidekt, "get_json", lambda url, **k: response)
        found = archidekt.search_owner_decks("Hugo0111", "Niv")
        assert [d["id"] for d in found] == [3, 2, 1]
        assert found[0]["url"] == "https://archidekt.com/decks/3"

    def test_find_marks_tracked_decks(self, archidekt_world):
        added = service.add_deck(str(NEW_ID), offline=True)
        found = service.find_decks("Hugo0111", "Niv")
        tracked = {d["id"]: d["tracked_as"] for d in found}
        assert tracked == {NEW_ID: added["slug"], 10078100: None}


class TestRemoveIsSafe:
    def test_refuses_anything_outside_decks(self, tmp_path):
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        (outside / "deck.json").write_text("{}")
        with pytest.raises(ValueError):
            deckfolder.remove(deckfolder.DeckFolder(slug="elsewhere", path=outside))
        assert outside.exists()

    def test_refuses_a_folder_that_is_not_a_deck(self):
        stray = deckfolder.decks_dir() / "stray"
        stray.mkdir(parents=True)
        with pytest.raises(ValueError):
            deckfolder.remove(deckfolder.DeckFolder(slug="stray", path=stray))
        assert stray.exists()
