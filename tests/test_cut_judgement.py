"""Regression tests for the Slinza failure.

After Nicolas edited his Slinza deck on Archidekt, `mtg deck suggest` offered
five cuts, each bad advice for a reason the tool had the data to see:

- The Great Henge and Blasphemous Act as "9 mana" top-of-curve cards — both
  discount themselves, and Slinza discounts every Beast by two besides.
- Garruk, Curse Breaker as redundant draw — the day it was added, and while
  engine.md listed it under "How it ends games".
- Radagast of Rhosgobel as redundant ramp — a second time, after Nicolas had
  cut other cards and kept it.
- Quartzwood Crasher on castability — two red sources short, in a deck with
  nine green sources to spare and seven Forests to trade.

Tracing those turned up three more: fight spells listed as win conditions,
Beast-token makers not counted as feeding the Beast tribe, and nothing stopping
a modal spell-land (a land slot) from being offered as redundant draw.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from mtgai import analysis, history
from mtgai.analysis import cost, curve, engine, report, suggest
from mtgai.model import CardEntry, Deck
from mtgai.sources import archidekt

FIXTURES = Path(__file__).parent / "fixtures"

SLINZA_TEXT = (
    "Beast spells you cast cost {2} less to cast.\n"
    "Each other Beast creature you control enters with an additional +1/+1 "
    "counter on it."
)


def card(name: str, cost_: str = "", mv: float = 0, type_line: str = "Sorcery", **kw) -> CardEntry:
    return CardEntry(name=name, mana_cost=cost_, mana_value=mv, type_line=type_line, **kw)


def slinza() -> CardEntry:
    return card(
        "Slinza, the Spiked Stampede", "{4}{G}", 5, "Legendary Creature — Beast",
        colors=["G"], color_identity=["R", "G"], is_commander=True,
        oracle_text=SLINZA_TEXT, tags=["typal-beast"],
    )


def boss() -> CardEntry:
    return card("Boss", "{G}", 1, "Legendary Creature — Human",
                color_identity=["G"], is_commander=True)


def deck_of(*cards: CardEntry) -> Deck:
    return Deck(slug="t", name="T", archidekt_id=1, cards=list(cards))


def playable(*cards: CardEntry) -> Deck:
    """A deck with lands enough that castability stays quiet."""
    return deck_of(
        *cards,
        card("Forest", "", 0, "Basic Land — Forest", quantity=30, mana_production={"G": 1}),
        card("Mountain", "", 0, "Basic Land — Mountain", quantity=15,
             mana_production={"R": 1}),
    )


def draw_deck(*extra: CardEntry) -> Deck:
    """Thirteen interchangeable draw spells: draw is oversupplied, and the
    representative the tool offers is "Draw 00" unless something outranks it.

    The commander asks for counters, so the engine's core is counters — with no
    wants at all, the biggest cluster (draw) would be core and never offered.
    """
    commander = card("Counter Boss", "{G}", 1, "Legendary Creature — Human",
                     color_identity=["G"], is_commander=True, tags=["gives pp counters"])
    counters = [card(f"Counter {i}", "{1}{G}", 2, tags=["gives pp counters"])
                for i in range(5)]
    draws = [card(f"Draw {i:02d}", "{2}{G}", 3, tags=["draw"]) for i in range(13)]
    return playable(commander, *counters, *draws, *extra)


def evidence_for(cuts: dict[str, dict], evidence: str) -> list[str]:
    return [name for name, c in cuts.items() if c["evidence"] == evidence]


def cut_names(deck: Deck, result: dict | None = None) -> dict[str, dict]:
    result = result or analysis.analyse(deck, offline=True)
    return {c["name"]: c for c in report._build_cuts(deck, result)}


class TestRealCost:
    def test_spells_that_discount_themselves_are_recognised(self):
        henge = card(
            "The Great Henge", "{7}{G}{G}", 9, "Legendary Artifact",
            oracle_text="This spell costs {X} less to cast, where X is the greatest "
            "power among creatures you control.\n{T}: Add {G}{G}. You gain 2 life.",
        )
        act = card(
            "Blasphemous Act", "{8}{R}", 9,
            oracle_text="This spell costs {1} less to cast for each creature on the "
            "battlefield.\nBlasphemous Act deals 13 damage to each creature.",
        )
        frogmite = card(
            "Frogmite", "{4}", 4, "Artifact Creature — Frog",
            oracle_text="Affinity for artifacts (This spell costs {1} less to cast "
            "for each artifact you control.)",
        )
        crescendo = card(
            "Harmonized Crescendo", "{4}{U}{U}", 6, keywords=["Convoke"],
            oracle_text="Convoke (Your creatures can help cast this spell. Each "
            "creature you tap while casting this spell pays for {1} or one mana "
            "of that creature's color.)\nChoose a creature type. Draw a card for "
            "each permanent you control of that type.",
        )
        assert cost.self_discounting(henge)
        assert cost.self_discounting(act)
        assert cost.self_discounting(frogmite)
        assert not cost.self_discounting(crescendo), "convoke changes who pays, not the cost"

    def test_the_commanders_discount_reduces_only_generic_mana(self):
        hoof = card("Craterhoof Behemoth", "{5}{G}{G}{G}", 8, "Creature — Beast")
        baloth = card("Frenzied Baloth", "{G}{G}", 2, "Creature — Beast")
        bounty = card("Primeval Bounty", "{5}{G}", 6, "Enchantment")
        walker = card("Realmwalker", "{2}{G}", 3, "Creature — Shapeshifter",
                      keywords=["Changeling"])
        deck = deck_of(slinza(), hoof, baloth, bounty, walker)

        discounts = cost.commander_discounts(deck)
        assert [(d.what, d.amount) for d in discounts] == [("Beast", 2)]
        assert cost.effective_mana_value(hoof, discounts) == 6
        assert cost.effective_mana_value(baloth, discounts) == 2, "coloured pips never shrink"
        assert cost.effective_mana_value(bounty, discounts) == 6, "not a Beast"
        assert cost.effective_mana_value(walker, discounts) == 1, "a changeling is a Beast"
        assert cost.effective_mana_value(deck.commanders[0], discounts) == 5, (
            "the commander does not discount itself"
        )

    def test_a_conditional_discount_is_not_guessed_at(self):
        goreclaw = card(
            "Goreclaw, Terror of Qal Sisma", "{3}{G}", 4, "Legendary Creature — Bear",
            is_commander=True,
            oracle_text="Creature spells you cast with power 4 or greater cost {2} "
            "less to cast.",
        )
        assert cost.commander_discounts(deck_of(goreclaw)) == []

    def test_curve_cuts_are_judged_at_real_cost(self):
        henge = card("The Great Henge", "{7}{G}{G}", 9, "Legendary Artifact",
                     oracle_text="This spell costs {X} less to cast, where X is the "
                     "greatest power among creatures you control.")
        act = card("Blasphemous Act", "{8}{R}", 9,
                   oracle_text="This spell costs {1} less to cast for each creature "
                   "on the battlefield.")
        colossal = card("Colossal Beast", "{7}{G}{G}", 9, "Creature — Beast")
        big = card("Big Beast", "{5}{G}{G}", 7, "Creature — Beast")
        bounty = card("Primeval Bounty", "{5}{G}", 6, "Enchantment")
        fillers = [card(f"Ponderous {i:02d}", "{4}{G}", 5) for i in range(12)]
        deck = playable(slinza(), henge, act, colossal, big, bounty, *fillers)

        cuts = cut_names(deck)
        # Henge and Act never qualify; Big Beast costs 5 with Slinza out, which
        # is not the top of anything.
        assert evidence_for(cuts, "curve") == ["Colossal Beast", "Primeval Bounty"]
        assert "9 mana (7 with Slinza's Beast discount)" in cuts["Colossal Beast"]["why"]

    def test_the_top_heavy_finding_counts_real_cost(self):
        henge = card("The Great Henge", "{7}{G}{G}", 9,
                     oracle_text="This spell costs {X} less to cast.")
        fillers = [card(f"Ponderous {i:02d}", "{4}{G}", 5) for i in range(13)]
        beasts = [card(f"Beast {i}", "{4}{G}{G}", 6, "Creature — Beast") for i in range(3)]
        result = curve.analyse(deck_of(slinza(), henge, *fillers, *beasts))
        assert result["expensive_spells"] == 18
        assert result["effective_expensive"] == 14
        assert (
            "18 spells at 5+ mana (14 counting Slinza's Beast discount and spells "
            "that discount themselves) is top-heavy" in " ".join(result["findings"])
        )

    def test_a_curve_that_only_looks_heavy_says_so(self):
        beasts = [card(f"Beast {i:02d}", "{3}{G}{G}", 5, "Creature — Beast") for i in range(13)]
        result = curve.analyse(deck_of(slinza(), *beasts))
        assert result["effective_expensive"] == 1, "only Slinza itself"
        assert any("lighter than it looks" in f for f in result["findings"])


class TestTheReportAgreesWithItself:
    def test_a_win_condition_is_never_offered_as_redundant(self):
        garruk = card("Garruk, Curse Breaker", "{3}{G}{G}", 5,
                      "Legendary Planeswalker — Garruk", tags=["draw", "overrun"])
        deck = draw_deck(garruk)
        result = analysis.analyse(deck, offline=True)
        assert "Garruk, Curse Breaker" in {
            w["name"] for w in result["engine"]["win_conditions"]
        }
        cuts = cut_names(deck, result)
        assert "Garruk, Curse Breaker" not in cuts
        assert cuts["Draw 00"]["evidence"] == "oversupplied: draw"

    def test_a_modal_spell_land_is_never_offered_as_redundant(self):
        restoration = card(
            "Sea Gate Restoration // Sea Gate, Reborn", "{4}{G}{G}{G}", 7,
            layout="modal_dfc", mana_production={"G": 1}, tags=["draw"],
        )
        assert restoration.is_modal_land
        cuts = cut_names(draw_deck(restoration))
        assert "Sea Gate Restoration // Sea Gate, Reborn" not in cuts
        assert "Draw 00" in cuts

    def test_a_role_at_its_minimum_keeps_its_cards(self):
        predation = card("Ezuri's Predation", "{5}{G}{G}{G}", 8, tags=["sweeper-one-sided"])
        rout = card("Rout", "{3}{G}{G}", 5, tags=["board-wipe"])
        fillers = [card(f"Ponderous {i:02d}", "{4}{G}", 5) for i in range(13)]
        deck = playable(boss(), predation, rout, *fillers)
        assert "Ezuri's Predation" not in cut_names(deck), (
            "two sweepers is the usual minimum; cutting one leaves the deck short"
        )

        third = card("Wrath", "{2}{G}{G}", 4, tags=["board-wipe"])
        deck = playable(boss(), predation, rout, third, *fillers)
        assert cut_names(deck)["Ezuri's Predation"]["evidence"] == "curve"

    def test_fight_and_bite_spells_do_not_end_games(self):
        stomp = card("Stump Stomp // Burnwillow Clearing", "{1}{R/G}", 2,
                     oracle_text="Target creature you control deals damage equal to "
                     "its power to target creature or planeswalker you don't control.")
        ambush = card("Khalni Ambush // Khalni Territory", "{2}{G}", 3, "Instant",
                      oracle_text="Target creature you control fights target creature "
                      "you don't control. (Each deals damage equal to its power to "
                      "the other.)")
        herald = card("Herald of Ilharg", "{2}{R}{G}", 4, "Creature — Boar Beast",
                      oracle_text="Trample\nWhenever you cast a creature spell, put two "
                      "+1/+1 counters on this creature. If that spell has mana value 5 "
                      "or greater, this creature deals damage equal to the number of "
                      "counters on it to each opponent.")
        fling = card("Fling", "{1}{R}", 2, "Instant",
                     oracle_text="As an additional cost to cast this spell, sacrifice a "
                     "creature.\nFling deals damage equal to the sacrificed creature's "
                     "power to any target.")
        deck = deck_of(boss(), stomp, ambush, herald, fling)
        names = {w["name"] for w in engine.win_condition_inventory(deck)}
        assert names == {"Herald of Ilharg", "Fling"}


def gruul(forests: int) -> Deck:
    """Slinza with one {R}{R} Beast: 18 red sources against the 20 it wants."""
    beasts = [card(f"Beast {i}", "{2}{G}", 3, "Creature — Beast", colors=["G"])
              for i in range(3)]
    lands = [
        card("Forest", "", 0, "Basic Land — Forest", quantity=forests,
             mana_production={"G": 1}),
        card("Mountain", "", 0, "Basic Land — Mountain", quantity=10,
             mana_production={"R": 1}),
        *[card(f"Dual {i}", "", 0, "Land", mana_production={"R": 1, "G": 1})
          for i in range(8)],
    ]
    return deck_of(
        slinza(),
        card("Quartzwood Crasher", "{2}{R}{R}{G}", 5, "Creature — Dinosaur Beast",
             colors=["R", "G"]),
        card("Craterhoof Behemoth", "{5}{G}{G}{G}", 8, "Creature — Beast", colors=["G"]),
        *beasts,
        *lands,
    )


class TestAFreeManaFixBeatsACut:
    def test_spare_basics_turn_the_sole_driver_into_a_mana_fix(self):
        # Green: 20 Forests + 8 duals = 28 sources against the 23 GGG needs.
        deck = gruul(forests=20)
        result = engine.analyse(deck)
        assert "Quartzwood Crasher" not in {c["name"] for c in result["castability_cuts"]}
        fix = next(f for f in result["mana_fixes"] if f["color"] == "R")
        swap = fix["basic_swap"]
        assert (swap["count"], swap["remove"], swap["add"]) == (2, "Forest", "Mountain")
        assert (swap["donor_left"], swap["donor_needed"]) == (26, 23)

        full = analysis.analyse(deck, offline=True)
        text = report.render_suggestions(deck, full, offline=True)
        assert "free fix: swap 2 Forest for 2 Mountain" in text

    def test_without_a_spare_colour_the_sole_driver_is_still_the_cut(self):
        # Green: 15 Forests + 8 duals = 23, exactly what GGG needs — nothing to give.
        result = engine.analyse(gruul(forests=15))
        cuts = {c["name"]: c for c in result["castability_cuts"]}
        assert cuts["Quartzwood Crasher"]["alternative"] == "add 2 Red sources instead"


class TestTribeTokens:
    def test_a_card_that_makes_the_tribe_feeds_it(self):
        garruk = card(
            "Garruk, Primal Hunter", "{2}{G}{G}{G}", 5, "Legendary Planeswalker — Garruk",
            oracle_text="+1: Create a 3/3 green Beast creature token.\n−3: Draw cards "
            "equal to the greatest power among creatures you control.",
            tags=["draw"],
        )
        plunderer = card("Pitiless Plunderer", "{3}{B}", 4, "Creature — Human Pirate",
                         oracle_text="Whenever another creature you control dies, "
                         "create a Treasure token.")
        beasts = [card(f"Beast {i}", "{2}{G}", 3, "Creature — Beast") for i in range(4)]
        deck = deck_of(slinza(), garruk, plunderer, *beasts)
        engine.analyse(deck)
        assert "typal" in garruk.engine_participation
        assert "typal" not in plunderer.engine_participation


def named(*names: str, history_: dict | None = None,
          updated_at: str = "2026-09-28T20:26:27Z") -> Deck:
    return Deck(slug="t", name="T", archidekt_id=1, updated_at=updated_at,
                cards=[card(n) for n in names], history=history_ or {})


class TestHistory:
    def test_a_first_import_has_nothing_to_remember(self):
        assert history.derive(None, named("A"), ["A"]) == {}

    def test_a_refresh_records_what_changed_and_what_was_kept(self):
        before = named("A", "B", "C", "D", updated_at="2026-09-22T15:50:53Z")
        h = history.derive(before, named("A", "B", "C", "E"), ["B", "D"])
        assert h["added"] == ["E"]
        assert h["removed"] == ["D"]
        assert h["kept_after_suggested_cut"] == {"B": "2026-09-28"}

    def test_nothing_counts_as_kept_until_something_is_cut(self):
        h = history.derive(named("A", "B"), named("A", "B", "E"), ["B"])
        assert h["added"] == ["E"]
        assert h["kept_after_suggested_cut"] == {}

    def test_a_refresh_with_no_changes_keeps_the_last_change(self):
        before = named("A", "B", history_={
            "added": ["B"], "removed": ["Z"],
            "kept_after_suggested_cut": {"A": "2026-09-01"},
        })
        h = history.derive(before, named("A", "B"), [])
        assert (h["added"], h["removed"]) == (["B"], ["Z"])
        assert h["kept_after_suggested_cut"] == {"A": "2026-09-01"}

    def test_a_kept_card_is_forgotten_once_it_leaves(self):
        before = named("A", "B", history_={"kept_after_suggested_cut": {"A": "2026-09-01"}})
        assert history.derive(before, named("B", "C"), [])["kept_after_suggested_cut"] == {}

    def test_only_judgement_is_remembered(self):
        deck = named("A", "B", "C", "D")
        offered = [
            {"name": "A", "evidence": "castability"},
            {"name": "B", "evidence": "oversupplied: draw"},
            {"name": "C", "evidence": "curve"},
            {"name": "D", "evidence": "low inclusion (weak signal)"},
        ]
        assert history.record_cuts(deck, offered) is True
        assert deck.history["suggested_cuts"] == ["B", "C"]
        assert history.record_cuts(deck, offered) is False, "unchanged — no rewrite"

    def test_history_survives_deck_json(self):
        deck = named("A", history_={"added": ["A"]})
        restored = Deck.from_dict(json.loads(json.dumps(deck.to_dict())))
        assert restored.history == {"added": ["A"]}

    def test_reports_written_before_history_are_read_back(self):
        payload = json.loads((FIXTURES / "archidekt_uugguu.json").read_text())
        deck = archidekt.normalise(payload, "uugguu-fixture", enrich=False)
        result = analysis.analyse(deck, offline=True)
        built = suggest.build(deck, result, offline=True)
        markdown = report.render_suggestions(deck, result, built, offline=True)
        expected = [c["name"] for c in built["cuts"] if history.is_soft(c["evidence"])]
        assert expected, "the fixture should offer soft cuts"
        assert history.parse_suggested_cuts(markdown) == expected


class TestDecisionsAreRespected:
    def test_a_kept_card_is_not_offered_again_on_soft_evidence(self):
        deck = draw_deck()
        deck.history = {"kept_after_suggested_cut": {"Draw 00": "2026-09-22"}}
        result = analysis.analyse(deck, offline=True)
        cuts = cut_names(deck, result)
        assert "Draw 00" not in cuts
        assert cuts["Draw 01"]["evidence"] == "oversupplied: draw"
        built = suggest.build(deck, result, offline=True)
        assert {"name": "Draw 00", "why": history.KEPT} in built["decided"]
        assert "Your call" in report.render_suggestions(deck, result, built, offline=True)

    def test_a_just_added_card_is_not_offered_on_soft_evidence(self):
        deck = draw_deck()
        deck.history = {"added": ["Draw 00"]}
        assert "Draw 00" not in cut_names(deck)

    def test_flexible_cards_reopen_a_decision(self):
        deck = draw_deck()
        deck.history = {"kept_after_suggested_cut": {"Draw 00": "2026-09-22"}}
        result = analysis.analyse(deck, offline=True)
        result["intent"] = {"flexible_cards": ["Draw 00"]}
        assert "Draw 00" in cut_names(deck, result)

    def test_a_decision_never_hides_a_fact(self):
        off = card("Off Colour", "{B}", 1, color_identity=["B"])
        deck = deck_of(boss(), off)
        deck.history = {"added": ["Off Colour"]}
        assert cut_names(deck)["Off Colour"]["evidence"] == "colour identity"


def _without(payload: dict, name: str) -> dict:
    trimmed = copy.deepcopy(payload)
    trimmed["cards"] = [
        c for c in trimmed["cards"] if c["card"]["oracleCard"]["name"] != name
    ]
    return trimmed


class TestRefreshLearnsFromTheEdit:
    @pytest.fixture
    def payloads(self):
        full = json.loads((FIXTURES / "archidekt_uugguu.json").read_text())
        # First sync lacks Mirror Image; the edit adds it and cuts Village Rites.
        return _without(full, "Mirror Image"), _without(full, "Village Rites")

    def test_a_refresh_remembers_what_was_added_cut_and_kept(self, monkeypatch, payloads):
        from mtgai import deckfolder, service

        first, second = payloads
        monkeypatch.setattr(archidekt, "fetch_raw", lambda deck_id, use_cache=True: first)
        service.add_deck("25569889", offline=True)
        folder = deckfolder.resolve("uugguu")
        offered = folder.read_deck().history["suggested_cuts"]
        assert offered, "the first sync should offer soft cuts"

        monkeypatch.setattr(archidekt, "fetch_raw", lambda deck_id, use_cache=True: second)
        service.refresh_deck("uugguu", offline=True)
        deck = folder.read_deck()
        assert deck.history["added"] == ["Mirror Image"]
        assert deck.history["removed"] == ["Village Rites"]
        kept = set(deck.history["kept_after_suggested_cut"])
        assert kept == {n for n in offered if n != "Village Rites"}

        text = folder.suggestions_path.read_text()
        section = text.split("## Consider cutting")[1].split("\n## ")[0]
        listed = {
            line.split("**")[1] for line in section.splitlines() if line.startswith("- **")
        }
        assert not listed & kept, "a kept card is not offered again on the same evidence"
        assert "Ayara, First of Locthwain" in listed, "castability is a fact, not a judgement"
        assert "Your call" in section

    def test_a_renamed_deck_keeps_its_history(self, monkeypatch, payloads):
        from mtgai import deckfolder, service

        first, second = payloads
        monkeypatch.setattr(archidekt, "fetch_raw", lambda deck_id, use_cache=True: first)
        service.add_deck("25569889", offline=True)

        renamed = copy.deepcopy(second)
        renamed["name"] = "Uugguu Reborn"
        monkeypatch.setattr(archidekt, "fetch_raw", lambda deck_id, use_cache=True: renamed)
        result = service.add_deck("25569889", offline=True)
        deck = deckfolder.folder_for(result["slug"]).read_deck()
        assert deck.history["removed"] == ["Village Rites"]


class TestFlexibleOutranksInference:
    def test_a_declared_flexible_card_is_never_shielded(self):
        garruk = card("Garruk, Curse Breaker", "{3}{G}{G}", 5,
                      "Legendary Planeswalker — Garruk", tags=["draw", "overrun"])
        deck = draw_deck(garruk)
        result = analysis.analyse(deck, offline=True)
        result["intent"] = {"flexible_cards": ["Garruk, Curse Breaker"]}
        assert cut_names(deck, result)["Garruk, Curse Breaker"]["evidence"] == "oversupplied: draw"


class TestStaplesAreNotOrphans:
    """Teferi's Protection, Lightning Greaves and Vampiric Tutor were offered
    as "doing nothing" in Edgar's deck: outside every cluster, but each doing a
    job every deck needs. Too many of a staple is the oversupply check's call."""

    def build(self) -> Deck:
        shields = [card(f"Shield {i}", "{1}{G}", 2, "Instant", tags=["protection"])
                   for i in range(3)]
        # Filed under Protection in Archidekt; its tags only say "change target".
        owned = card("Deflecting Swat", "{2}{G}", 3, "Instant", categories=["Protection"])
        oddity = card("Oddity", "{2}{G}", 3, "Enchantment")
        return playable(boss(), *shields, owned, oddity)

    def test_a_staple_outside_the_theme_is_not_offered_as_doing_nothing(self):
        deck = self.build()
        result = analysis.analyse(deck, offline=True)
        assert "Shield 0" in result["engine"]["orphans"], "still listed for information"
        cuts = cut_names(deck, result)
        assert not {"Shield 0", "Shield 1", "Shield 2", "Deflecting Swat"} & set(cuts)
        assert cuts["Oddity"]["evidence"] == "no engine participation"

    def test_a_token_doubler_is_a_token_card(self):
        from mtgai import tags

        procession = card("Anointed Procession", "{3}{W}", 4, "Enchantment",
                          tags=["token doubler", "token increaser"])
        assert "tokens" in tags.card_categories(procession)
