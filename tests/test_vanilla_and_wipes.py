"""Regression tests for the Jasmine failure, and the "0 wipe" report.

Jasmine Boreal of the Seven's mana casts only creature spells with no
abilities, and her creatures with no abilities can't be blocked by creatures
with abilities. The tool read her deck as "ramp" with a Human tribe — Jasmine
is a Human Druid and five Humans happened to be in it — and listed thirty
cards as doing nothing: every vanilla creature and every anthem, the deck's
entire engine. It would have offered Isamaru and Leatherback Baloth as cuts.
It also could not see that Asceticism, by giving the whole team hexproof,
switches Jasmine off.

Separately, Slinza's report said "0 wipe" about a deck running Blasphemous Act
and Ezuri's Predation: the oracle patterns missed "deals 13 damage to each
creature", and two tagged sweepers were too few to count as a cluster, so the
tags were never consulted.
"""

from __future__ import annotations

from mtgai import analysis
from mtgai.analysis import cost, engine, report, roles
from mtgai.model import CardEntry, Deck

JASMINE_TEXT = (
    "{T}: Add {G}{W}. Spend this mana only to cast creature spells with no "
    "abilities.\nCreatures you control with no abilities can't be blocked by "
    "creatures with abilities."
)


def card(name: str, cost_: str = "", mv: float = 0, type_line: str = "Sorcery", **kw) -> CardEntry:
    return CardEntry(name=name, mana_cost=cost_, mana_value=mv, type_line=type_line, **kw)


def deck_of(*cards: CardEntry) -> Deck:
    return Deck(slug="t", name="T", archidekt_id=1, cards=list(cards))


def jasmine() -> CardEntry:
    return card("Jasmine Boreal of the Seven", "{1}{G}{W}", 3,
                "Legendary Creature — Human Druid", colors=["G", "W"],
                color_identity=["G", "W"], is_commander=True, oracle_text=JASMINE_TEXT,
                mana_production={"G": 1, "W": 1}, tags=["mana dork", "ramp"])


def vanillas(n: int = 6, legendary: bool = False) -> list[CardEntry]:
    cards = [card(f"Vanilla {i}", "{1}{G}", 2, "Creature — Bear", colors=["G"])
             for i in range(n)]
    if legendary:
        cards.append(card("Isamaru, Hound of Konda", "{W}", 1,
                          "Legendary Creature — Dog", colors=["W"]))
    return cards


def lands() -> list[CardEntry]:
    return [
        card("Forest", "", 0, "Basic Land — Forest", quantity=20, mana_production={"G": 1}),
        card("Plains", "", 0, "Basic Land — Plains", quantity=15, mana_production={"W": 1}),
    ]


class TestWipes:
    def test_damage_and_shrink_sweepers_are_wipes(self):
        act = card("Blasphemous Act", "{8}{R}", 9,
                   oracle_text="This spell costs {1} less to cast for each creature "
                   "on the battlefield.\nBlasphemous Act deals 13 damage to each creature.")
        anger = card("Anger of the Gods", "{1}{R}{R}", 3,
                     oracle_text="Anger of the Gods deals 3 damage to each creature. If "
                     "a creature dealt damage this way would die this turn, exile it instead.")
        norn = card("Elesh Norn, Grand Cenobite", "{5}{W}{W}", 7,
                    "Legendary Creature — Phyrexian Praetor",
                    oracle_text="Vigilance\nOther creatures you control get +2/+2.\n"
                    "Creatures your opponents control get -2/-2.")
        bolt = card("Lightning Bolt", "{R}", 1, "Instant",
                    oracle_text="Lightning Bolt deals 3 damage to any target.")
        assert roles.WIPE in roles.classify(act)
        assert roles.WIPE in roles.classify(anger)
        assert roles.WIPE in roles.classify(norn)
        assert roles.WIPE not in roles.classify(bolt)

    def test_sweepers_too_few_for_a_cluster_still_count(self):
        boss = card("Boss", "{G}", 1, "Legendary Creature — Human", is_commander=True)
        sweepers = [card(f"Wipe {i}", "{3}{G}", 4, oracle_text="Something happens.",
                         tags=["sweeper"]) for i in range(2)]
        result = analysis.analyse(deck_of(boss, *sweepers, *lands()), offline=True)
        assert result["roles"]["tag_counts"]["wipe"] == 2
        assert not [f for f in result["roles"]["findings"] if f["role"] == roles.WIPE], (
            "two tagged sweepers meet the usual minimum of two"
        )

    def test_one_tagged_sweeper_is_still_short_and_says_so(self):
        boss = card("Boss", "{G}", 1, "Legendary Creature — Human", is_commander=True)
        sweeper = card("Wipe", "{3}{G}", 4, oracle_text="Something happens.",
                       tags=["sweeper"])
        result = analysis.analyse(deck_of(boss, sweeper, *lands()), offline=True)
        wipe = next(f for f in result["roles"]["findings"] if f["role"] == roles.WIPE)
        assert "(tags count 1)" in wipe["message"]


class TestNoAbilities:
    def test_a_creature_with_no_rules_text_has_no_abilities(self):
        assert card("Savannah Lions", "{W}", 1, "Creature — Cat").has_no_abilities
        assert not card("Trampler", "{G}", 1, "Creature — Beast",
                        oracle_text="Trample").has_no_abilities
        assert not card("Nothing", "{G}", 1, "Sorcery").has_no_abilities

    def test_an_adventurer_is_judged_by_its_creature_face(self):
        # The import drops empty faces, so no separator means the creature
        # face was blank and only the Adventure half has text.
        mouse = card("Cheeky House-Mouse // Squeak By", "{W} // {W}", 1,
                     "Creature — Mouse", layout="adventure",
                     oracle_text="Target creature you control gets +1/+1 until end of turn.")
        giant = card("Bonecrusher Giant // Stomp", "{2}{R} // {1}{R}", 3,
                     "Creature — Giant", layout="adventure",
                     oracle_text="Whenever this creature becomes the target of a spell, it "
                     "deals 2 damage to that spell's controller.\n//\nDamage can't be "
                     "prevented this turn. Stomp deals 2 damage to any target.")
        assert mouse.has_no_abilities
        assert not giant.has_no_abilities


class TestAVanillaCommanderIsUnderstood:
    def build(self) -> Deck:
        anthems = [card(f"Anthem {i}", "{1}{G}{W}", 3, "Enchantment",
                        oracle_text="Creatures you control get +1/+1.", tags=["anthem"])
                   for i in range(5)]
        petroglyphs = card("Muraganda Petroglyphs", "{3}{G}", 4, "Enchantment",
                           oracle_text="Creatures with no abilities get +2/+2.")
        bears = card("Den of Bears", "{3}{G}", 4, "Enchantment",
                     oracle_text="Landfall — Whenever a land you control enters, create a "
                     "2/2 green Bear creature token.")
        gift = card("Generous Gift", "{2}{W}", 3, "Instant",
                    oracle_text="Destroy target permanent. Its controller creates a 3/3 "
                    "green Elephant creature token.", tags=["removal"])
        humans = [card(f"Human {i}", "{W}", 1, "Creature — Human Soldier",
                       oracle_text="Lifelink") for i in range(4)]
        # A real deck's size: five Humans among ~75 spells is not a tribe.
        return deck_of(jasmine(), *vanillas(55), *anthems, petroglyphs, bears, gift,
                       *humans, *lands())

    def test_the_commander_asks_for_vanillas_and_anthems(self):
        wants = engine.commander_wants(self.build())
        assert {"vanilla", "anthem"} <= set(wants)

    def test_vanillas_and_their_payoffs_are_the_engine(self):
        deck = self.build()
        result = engine.analyse(deck)
        member = {c.name: c.engine_participation for c in deck.cards}
        assert "vanilla" in member["Vanilla 0"]
        assert "vanilla" in member["Muraganda Petroglyphs"]
        assert "vanilla" in member["Den of Bears"], "its Bears have no abilities"
        assert "vanilla" not in member["Generous Gift"], "that token is the opponent's"
        assert "anthem" in member["Anthem 0"]
        assert result["archetype"].startswith("vanilla")
        assert not {"Vanilla 0", "Muraganda Petroglyphs", "Anthem 0"} & set(result["orphans"])

    def test_a_commanders_creature_type_is_not_a_tribe(self):
        # Five Humans (Jasmine included) in a deck that does not care about them.
        assert engine.commander_tribe(self.build()) is None

    def test_a_token_with_a_keyword_is_not_vanilla(self):
        trampler = card("Garruk, Curse Breaker", "{3}{G}{G}", 5, "Legendary Planeswalker",
                        oracle_text="−3: Create a 4/4 green Beast creature token with trample.")
        assert not engine.makes_vanilla_tokens(trampler)


class TestAbilitiesCanBeACost:
    GRANTS = {
        "Asceticism": "Creatures you control have hexproof.\n{1}{G}: Regenerate target creature.",
        "Flowering of the White Tree": "Legendary creatures you control get +2/+1 and have "
        "ward {1}.\nNonlegendary creatures you control get +1/+1.",
    }
    HARMLESS = {
        "Heroic Intervention": "Permanents you control gain hexproof and indestructible "
        "until end of turn.",
        "Felidar Retreat": "Landfall — Whenever a land you control enters, put a +1/+1 "
        "counter on each creature you control. Those creatures gain vigilance until end "
        "of turn.",
        "Elesh Norn, Grand Cenobite": "Vigilance\nOther creatures you control get +2/+2.",
        "Turn to Frog": "Until end of turn, target creature loses all abilities and "
        "becomes a blue Frog with base power and toughness 1/1.",
        "Biomass": "Creatures you control have base power and toughness 3/3.",
    }

    def cards(self) -> list[CardEntry]:
        return [card(name, "{2}{G}", 3, "Enchantment", oracle_text=text)
                for name, text in {**self.GRANTS, **self.HARMLESS}.items()]

    def test_giving_the_team_an_ability_switches_jasmine_off(self):
        deck = deck_of(jasmine(), *vanillas(legendary=True), *self.cards(), *lands())
        cuts = {c["name"]: c for c in report._build_cuts(deck, analysis.analyse(deck, offline=True))}
        flagged = {name for name, c in cuts.items() if c["evidence"] == "anti-synergy"}
        assert flagged == set(self.GRANTS)
        assert "hexproof" in cuts["Asceticism"]["why"]

    def test_a_grant_to_legendaries_matters_only_with_a_legendary_vanilla(self):
        deck = deck_of(jasmine(), *vanillas(legendary=False), *self.cards(), *lands())
        flagged = {a["name"] for a in engine.analyse(deck)["anti_synergy"]}
        assert flagged == {"Asceticism"}

    def test_other_commanders_are_left_alone(self):
        boss = card("Boss", "{G}", 1, "Legendary Creature — Human", is_commander=True)
        deck = deck_of(boss, *vanillas(), *self.cards(), *lands())
        assert engine.analyse(deck)["anti_synergy"] == []


class TestCommanderMana:
    def test_jasmines_mana_pays_for_vanillas_only(self):
        big_vanilla = card("Vorstclaw", "{4}{G}{G}", 6, "Creature — Elemental Horror")
        big_other = card("Thragtusk", "{4}{G}", 5, "Creature — Beast",
                         oracle_text="When this creature enters, you gain 5 life.")
        deck = deck_of(jasmine(), big_vanilla, big_other)
        discounts = cost.commander_discounts(deck)
        assert [(d.amount, d.mana, d.no_abilities) for d in discounts] == [(2, True, True)]
        assert discounts[0].phrase == "Jasmine Boreal of the Seven's mana"
        assert cost.effective_mana_value(big_vanilla, discounts) == 4
        assert cost.effective_mana_value(big_other, discounts) == 5


class TestTribeNeedsTheDeck:
    def humans(self, n: int) -> list[CardEntry]:
        return [card(f"Human {i}", "{W}", 1, "Creature — Human", oracle_text="Lifelink")
                for i in range(n)]

    def others(self, n: int) -> list[CardEntry]:
        return [card(f"Other {i}", "{G}", 1, "Creature — Bear", oracle_text="Reach")
                for i in range(n)]

    def test_a_handful_of_the_commanders_type_is_not_a_tribe(self):
        commander = card("Druid Boss", "{G}{W}", 2, "Legendary Creature — Human Druid",
                         is_commander=True)
        deck = deck_of(commander, *self.humans(4), *self.others(60))
        assert engine.commander_tribe(deck) is None

    def test_a_deck_built_of_it_still_is(self):
        commander = card("Human Boss", "{G}{W}", 2, "Legendary Creature — Human Noble",
                         is_commander=True)
        deck = deck_of(commander, *self.humans(20), *self.others(50))
        assert engine.commander_tribe(deck) == "Human"

    def test_tag_evidence_keeps_the_low_bar(self):
        commander = card("Beast Boss", "{G}", 1, "Legendary Creature — Elf",
                         is_commander=True, tags=["typal-beast"])
        beasts = [card(f"Beast {i}", "{G}", 1, "Creature — Beast") for i in range(5)]
        deck = deck_of(commander, *beasts, *self.others(60))
        assert engine.commander_tribe(deck) == "Beast"
