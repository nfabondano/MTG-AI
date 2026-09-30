"""Advice the tool gave that had to be thrown away by hand — never again.

Each class is one row of that list, pinned against the real deck it happened
on (trimmed Archidekt payloads in tests/fixtures). If one of these fails, the
tool is about to repeat a mistake it already made in front of Nicolas.
"""

from __future__ import annotations

from mtgai import tags
from mtgai.analysis import engine
from mtgai.model import CardEntry, Deck


def _engine(deck: Deck, intent=None) -> dict:
    tags.ensure_tags(deck)
    return engine.analyse(deck, intent)


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
