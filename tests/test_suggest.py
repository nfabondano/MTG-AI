"""Structured suggestions: grouped adds, earned pairs, and the dossier.

The doctrine under test: popularity alone never reaches the default output —
an add in the first two groups must carry a deck-internal or
commander-specific reason; a paired swap must share a job with its cut; the
deck-internal add engine finds candidates through the local Tagger and
Scryfall caches; and engine.md now says how the deck actually plays and wins.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from mtgai import analysis
from mtgai.analysis import engine, report, suggest
from mtgai.model import CardEntry, Deck
from mtgai.sources import archidekt, scryfall, tagger

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def uugguu() -> Deck:
    payload = json.loads((FIXTURES / "archidekt_uugguu.json").read_text())
    return archidekt.normalise(payload, "uugguu-fixture", enrich=False)


@pytest.fixture
def result(uugguu) -> dict:
    return analysis.analyse(uugguu, offline=True)


class TestGroups:
    def test_first_two_groups_never_lean_on_popularity_alone(self, uugguu, result):
        result["edhrec"] = {
            "available": True,
            "missing_staples": [
                {"name": "Rejuvenating Springs", "inclusion": 0.5, "list": "Lands"}
            ],
            "missing_synergy": [],
            "off_meta": [],
        }
        adds = suggest.build_adds(uugguu, result, offline=True)
        for entry in adds:
            if entry["group"] in (suggest.GROUP_PLAN, suggest.GROUP_WEAKNESS):
                assert "popularity only" not in entry["why"]
                assert entry["source"] != "edhrec-staple"

    def test_inclusion_only_candidates_are_meta_optional(self, uugguu, result):
        result["edhrec"] = {
            "available": True,
            "missing_staples": [{"name": "Mitotic Slime", "inclusion": 0.61}],
            "missing_synergy": [],
            "off_meta": [],
        }
        adds = suggest.build_adds(uugguu, result, offline=True)
        mitotic = next(a for a in adds if a["name"] == "Mitotic Slime")
        assert mitotic["group"] == suggest.GROUP_META

    def test_synergy_joins_the_plan_with_its_population_named(self, uugguu, result):
        result["edhrec"] = {
            "available": True,
            "basis_label": "vs 115 Clones builds of Uugguu, the Omniplasm",
            "missing_staples": [],
            "missing_synergy": [{"name": "Stunt Double", "synergy": 0.35}],
            "off_meta": [],
        }
        adds = suggest.build_adds(uugguu, result, offline=True)
        stunt = next(a for a in adds if a["name"] == "Stunt Double")
        assert stunt["group"] == suggest.GROUP_PLAN
        assert "115 Clones builds" in stunt["why"]

    def test_meta_group_is_hidden_from_the_default_render(self, uugguu, result):
        result["edhrec"] = {
            "available": True,
            "missing_staples": [{"name": "Mitotic Slime", "inclusion": 0.61}],
            "missing_synergy": [],
            "off_meta": [],
        }
        strict = report.render_suggestions(uugguu, result, offline=True)
        loose = report.render_suggestions(uugguu, result, loose=True, offline=True)
        assert "Mitotic Slime" not in strict
        assert "popularity-only" in strict  # the hidden count is announced
        assert "Mitotic Slime" in loose


class TestInternalNeeds:
    def test_a_thin_tribe_asks_for_fuel(self, uugguu, result):
        result["engine"]["tribe_census"] = {
            "tribe": "Ooze", "true_type": 6, "changelings": 2, "conditional_copies": 9,
        }
        needs = suggest.internal_needs(uugguu, result)
        fuel = [n for n in needs if n["kind"] == "fuel"]
        assert fuel and "8 real Oozes" in fuel[0]["why"]

    def test_a_deep_tribe_asks_for_nothing(self, uugguu, result):
        needs = suggest.internal_needs(uugguu, result)
        assert not [n for n in needs if n["kind"] == "fuel"]

    def test_candidates_come_from_the_local_caches_and_respect_identity(
        self, uugguu, result
    ):
        # Seed the shared sqlite with one legal candidate and one off-colour one.
        conn = sqlite3.connect(scryfall.scryfall_db_path())
        conn.execute(
            "CREATE TABLE IF NOT EXISTS cards (oracle_id TEXT PRIMARY KEY, name TEXT, name_lower TEXT, data TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS card_tags (oracle_id TEXT, tag TEXT, PRIMARY KEY (oracle_id, tag))"
        )
        def seed(oid, name, identity):
            conn.execute(
                "INSERT OR REPLACE INTO cards VALUES (?, ?, ?, ?)",
                (oid, name, name.lower(), json.dumps({
                    "name": name,
                    "color_identity": identity,
                    "legalities": {"commander": "legal"},
                    "prices": {"usd": "1.00"},
                    "edhrec_rank": 100,
                })),
            )
            conn.execute("INSERT OR REPLACE INTO card_tags VALUES (?, ?)", (oid, "removal"))
        seed("oid-1", "Legal Removal Spell", ["B", "G"])
        seed("oid-2", "Off Colour Removal", ["R"])
        conn.commit()
        conn.close()

        need = {
            "kind": "role", "group": suggest.GROUP_WEAKNESS, "category": "removal",
            "tagger_tag": "removal", "why": "short", "evidence": "role shortage",
        }
        cards = suggest._candidates_for(
            need, uugguu, set(uugguu.color_identity()), budget=None, offline=True
        )
        names = [c["name"] for c in cards]
        assert "Legal Removal Spell" in names
        assert "Off Colour Removal" not in names


class TestSwaps:
    def test_pairs_share_a_job_and_nothing_is_positional(self, uugguu, result):
        adds = [
            {"name": "New Ramp Rock", "group": suggest.GROUP_WEAKNESS,
             "category": "ramp", "why": "", "evidence": "", "price": None},
            {"name": "Unrelated Combo Piece", "group": suggest.GROUP_PLAN,
             "category": None, "why": "", "evidence": "", "price": None},
        ]
        cuts = suggest.build_cuts(uugguu, result)
        swaps = suggest.pair_swaps(uugguu, adds, cuts)
        for swap in swaps:
            assert swap["shared"] == "ramp"
            assert swap["add"] == "New Ramp Rock"
        assert all(s["add"] != "Unrelated Combo Piece" for s in swaps)


class TestDossier:
    def test_engine_md_explains_role_wins_and_quadrants(self, uugguu, result):
        text = report.render_engine(result)
        assert "Ooze" in text
        assert "payoff" in text
        assert "## How it ends games" in text
        assert "## Quadrant coverage" in text

    def test_win_condition_inventory_reads_text_tags_and_owner_categories(self):
        cards = [
            CardEntry(name="Boss", type_line="Legendary Creature", is_commander=True),
            CardEntry(name="Approach", type_line="Sorcery",
                      oracle_text="You win the game."),
            CardEntry(name="Trample Anthem", type_line="Sorcery",
                      tags=["overrun"]),
            CardEntry(name="Pet Dragon", type_line="Creature",
                      categories=["Finisher"]),
            CardEntry(name="Bystander", type_line="Creature"),
        ]
        deck = Deck(slug="t", name="T", archidekt_id=1, cards=cards)
        names = {w["name"]: w["why"] for w in engine.win_condition_inventory(deck)}
        assert "Approach" in names
        assert "Trample Anthem" in names
        assert names["Pet Dragon"] == "your own category marks it the finisher"
        assert "Bystander" not in names

    def test_an_honest_line_when_no_closer_exists(self):
        cards = [
            CardEntry(name="Boss", type_line="Legendary Creature", is_commander=True),
            CardEntry(name="Cantrip", type_line="Instant", oracle_text="Draw a card."),
        ]
        deck = Deck(slug="t", name="T", archidekt_id=1, cards=cards)
        result = analysis.analyse(deck, offline=True)
        text = report.render_engine(result)
        assert "No clear closer found" in text


class TestServiceRoundTrip:
    def test_suggest_returns_markdown_and_structure(self, monkeypatch, uugguu):
        payload = json.loads((FIXTURES / "archidekt_uugguu.json").read_text())
        monkeypatch.setattr(archidekt, "fetch_raw", lambda deck_id, use_cache=True: payload)
        from mtgai import service

        service.add_deck("25569889", offline=True)
        out = service.suggest("uugguu", offline=True)
        assert {"markdown", "suggestions"} <= set(out)
        # 104 cards: the "which ones go" plan comes with it.
        assert out["trim"]["need"] == 4
        assert "## Consider cutting" in out["markdown"]
        built = out["suggestions"]
        assert {"mana_fixes", "adds", "cuts", "swaps", "hidden_meta"} <= set(built)
        assert any(f["color"] == "G" for f in built["mana_fixes"])
