"""The real regression decks load the way the tool will see them.

These three decks are where the tool kept giving advice that had to be thrown
away; the regression tests elsewhere lean on them, so first make sure the
fixtures themselves are what they claim to be.
"""

from __future__ import annotations

import pytest

from conftest import real_deck


@pytest.mark.parametrize(
    ("key", "commander", "total", "identity"),
    [
        ("niv", "Niv-Mizzet, Ghost Counsel", 100, ["W", "B"]),
        ("felisa", "Felisa, Fang of Silverquill", 106, ["W", "B"]),
        ("equipments", "Cloud, Ex-SOLDIER", 103, ["W", "R", "G"]),
    ],
)
def test_fixture_normalises(key, commander, total, identity):
    deck = real_deck(key)
    assert [c.name for c in deck.commanders] == [commander]
    assert deck.total_cards == total
    assert deck.color_identity() == identity


@pytest.mark.parametrize("key", ["niv", "felisa", "equipments"])
def test_fixture_keeps_archidekt_tags(key):
    """The regressions depend on the oTags Archidekt ships; trimming kept them."""
    deck = real_deck(key)
    tagged = [c for c in deck.cards if c.tags]
    assert len(tagged) > 0.8 * len(deck.cards)


def test_equipments_keeps_its_maybeboard():
    """The maybeboard holds the Game Changers a bracket-2 player is tempted by."""
    deck = real_deck("equipments")
    excluded = {e["name"] for e in deck.excluded}
    assert {"Teferi's Protection", "Smothering Tithe", "Aggravated Assault"} <= excluded
    assert deck.archidekt_bracket == 2
