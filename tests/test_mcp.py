"""The MCP server: the same service, answered slim and without surprises."""

from __future__ import annotations

import asyncio
import copy
import json

import pytest

from conftest import REAL_DECKS, load_fixture
from mtgai import mcp_server, service
from mtgai.http import NotFound
from mtgai.sources import archidekt


@pytest.fixture
def served(monkeypatch):
    """Archidekt serving the recorded real decks by id."""
    payloads = {}
    for key, (fixture, _) in REAL_DECKS.items():
        payload = load_fixture(fixture)
        payloads[int(payload["id"])] = payload
    gone: set[int] = set()

    def fetch_raw(deck_id, use_cache=True):
        if deck_id in gone or deck_id not in payloads:
            raise NotFound(f"not found: {deck_id}", status=404)
        return payloads[deck_id]

    monkeypatch.setattr(archidekt, "fetch_raw", fetch_raw)
    monkeypatch.setattr(
        archidekt, "get_json", lambda url, **k: load_fixture("archidekt_search_niv.json")
    )
    return {"payloads": payloads, "gone": gone}


def _call(name: str, arguments: dict):
    """Through the server, as a client would: arguments validated, output serialised."""
    return asyncio.run(mcp_server.mcp.call_tool(name, arguments))


def test_every_workflow_tool_is_registered():
    names = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
    assert {
        "deck_add", "deck_refresh", "deck_relink", "deck_find", "deck_analyze",
        "deck_suggest", "deck_trim", "deck_engine", "deck_intent", "deck_intent_set",
    } <= names


def test_the_instructions_carry_the_rules():
    text = mcp_server.INSTRUCTIONS
    for phrase in ("deck_trim", "popularity alone", "colour identity", "judgement call",
                   "intent.md", "uv run mtg"):
        assert phrase in text


def test_analyze_is_slim_unless_asked(served):
    slug = mcp_server.deck_add("6313712", offline=True)["slug"]
    slim = mcp_server.deck_analyze(slug, offline=True)
    full = mcp_server.deck_analyze(slug, offline=True, full=True)
    assert set(slim) >= {"headline", "engine", "bracket", "combos", "roles", "mana"}
    assert "profile" not in slim["engine"] and "profile" in full["engine"]
    assert len(json.dumps(slim, default=str)) * 2 < len(json.dumps(full, default=str))
    assert slim["bracket"]["estimate"] == 3


def test_trim_through_the_server(served):
    slug = mcp_server.deck_add("6313712", offline=True)["slug"]
    answer = _call("deck_trim", {"reference": slug, "offline": True})
    assert not answer.is_error
    plan = answer.structured_content
    assert plan["need"] == 6
    assert len(plan["cuts"]) == 6 and len(plan["extras"]) == 3
    assert plan["after"]["total"] == 100
    assert all(p["reason"] for p in plan["cuts"])


def test_suggest_carries_the_trim_for_an_oversized_deck(served):
    slug = mcp_server.deck_add("6313712", offline=True)["slug"]
    answer = mcp_server.deck_suggest(slug, offline=True)
    assert answer["trim"]["need"] == 6
    assert answer["max_bracket"] is None or answer["max_bracket"] >= 1


def test_suggest_defaults_to_the_declared_bracket(served):
    slug = mcp_server.deck_add("14435331", offline=True)["slug"]
    assert mcp_server.deck_suggest(slug, offline=True)["max_bracket"] == 2


def test_a_moved_deck_is_an_answer_not_an_error(served):
    old = copy.deepcopy(served["payloads"][26941717])
    old["id"] = 26909132
    served["payloads"][26909132] = old
    slug = mcp_server.deck_add("26909132", offline=True)["slug"]
    served["gone"].add(26909132)

    answer = _call("deck_refresh", {"reference": slug, "offline": True})
    assert not answer.is_error
    moved = answer.structured_content
    assert moved["status"] == "moved"
    assert moved["candidates"][0]["id"] == 26941717
    assert any("--follow" in step for step in moved["next"])

    followed = mcp_server.deck_refresh(slug, follow=True, offline=True)
    assert followed["status"] == "ok"
    assert followed["relinked"]["to"] == 26941717


def test_find_marks_what_is_tracked(served):
    slug = mcp_server.deck_add("26941717", offline=True)["slug"]
    found = {d["id"]: d["tracked_as"] for d in mcp_server.deck_find("Hugo0111", "Niv")}
    assert found[26941717] == slug
    assert found[10078100] is None


def test_summary_needs_no_network(served):
    """analysis_summary is pure: it only reshapes what analysis produced."""
    slug = mcp_server.deck_add("14435331", offline=True)["slug"]
    result = service.analyse_deck(slug, offline=True)
    summary = service.analysis_summary(result)
    assert summary["engine"]["archetype"].startswith("equipment")
    assert summary["combos"]["available"] is False
