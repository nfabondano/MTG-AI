"""Classify each card by the job it does in the deck.

Role counts are the backbone of EDH deckbuilding advice: "you have four ramp
spells and eleven board wipes" is a more useful sentence than any curve chart.

Detection is oracle-text pattern matching, which is approximate by nature. Two
things keep it honest: a card can hold several roles (Beast Within is removal;
Skullclamp is draw), and an explicit override table handles the cards the
patterns reliably get wrong.
"""

from __future__ import annotations

import re
from collections import Counter

from ..model import CardEntry, Deck

RAMP = "ramp"
DRAW = "draw"
REMOVAL = "removal"
WIPE = "wipe"
TUTOR = "tutor"
PROTECTION = "protection"
COUNTERSPELL = "counterspell"
RECURSION = "recursion"
GRAVEHATE = "graveyard hate"
LAND = "land"

# Normal ranges for a 100-card Commander deck. These are guidance, not law —
# a deck that ramps through rituals or draws off combat may sit outside them
# deliberately, so the report frames these as "worth a look", never as errors.
TARGETS: dict[str, tuple[int, int]] = {
    RAMP: (8, 12),
    DRAW: (8, 12),
    REMOVAL: (6, 10),
    WIPE: (2, 4),
    PROTECTION: (2, 6),
}

ROLE_ORDER = [RAMP, DRAW, REMOVAL, WIPE, COUNTERSPELL, PROTECTION, TUTOR, RECURSION, GRAVEHATE]

# Cards the text patterns misjudge. Kept small on purpose: every entry here is
# a rule the patterns could not express, not a card-by-card database.
OVERRIDES: dict[str, list[str]] = {
    "sol ring": [RAMP],
    "arcane signet": [RAMP],
    "chromatic lantern": [RAMP],
    "smothering tithe": [RAMP],
    "dockside extortionist": [RAMP],
    "skullclamp": [DRAW],
    "sylvan library": [DRAW],
    "rhystic study": [DRAW],
    "mystic remora": [DRAW],
    "esper sentinel": [DRAW],
    "phyrexian arena": [DRAW],
    "necropotence": [DRAW],
    "beast within": [REMOVAL],
    "generous gift": [REMOVAL],
    "chaos warp": [REMOVAL],
    "swords to plowshares": [REMOVAL],
    "path to exile": [REMOVAL],
    "cyclonic rift": [REMOVAL, WIPE],
    "teferi's protection": [PROTECTION],
    "heroic intervention": [PROTECTION],
    "lightning greaves": [PROTECTION],
    "swiftfoot boots": [PROTECTION],
    "birds of paradise": [RAMP],
    "llanowar elves": [RAMP],
    "cultivate": [RAMP],
    "kodama's reach": [RAMP],
    "rampant growth": [RAMP],
    "farseek": [RAMP],
    "nature's lore": [RAMP],
    "three visits": [RAMP],
    "demonic tutor": [TUTOR],
    "vampiric tutor": [TUTOR],
    "enlightened tutor": [TUTOR],
    "mystical tutor": [TUTOR],
}

# --- patterns -------------------------------------------------------------

_RAMP_PATTERNS = [
    r"search your library for a[^.]*\bland\b",
    r"\badd \{",
    # Cards that word their mana in prose rather than symbols — "Add one mana
    # of any color" (Phyrexian Altar) has no braces at all and would otherwise
    # never register as a mana source.
    r"\badd (?:one|two|three|four|X) mana\b",
    r"\badd mana of any\b",
    r"create a treasure token",
    r"play an additional land",
    r"put a land card[^.]*onto the battlefield",
    r"lands? you control[^.]*add",
]
_DRAW_PATTERNS = [
    r"\bdraws? (?:a|two|three|four|X|that many) card",
    r"\bdraw cards? equal",
    r"you may draw a card",
    r"investigate",
    r"exile the top card of your library[^.]*you may (?:play|cast)",
]
_REMOVAL_PATTERNS = [
    r"destroy target",
    r"exile target (?:creature|permanent|artifact|enchantment|planeswalker|nonland)",
    r"target creature gets -\d+/-\d+",
    r"deals? \d+ damage to target (?:creature|permanent|planeswalker|any target)",
    r"target (?:player|opponent) sacrifices",
    r"return target (?:creature|permanent|nonland permanent) to (?:its owner's|their owner's) hand",
    r"fight target creature",
    # Edicts. "Each opponent sacrifices a creature" removes one creature per
    # player — that is removal, not a board wipe, and counting it as a wipe
    # made sacrifice payoffs like Butcher of Malakir look like Wrath effects.
    r"each (?:player|opponent) sacrifices a(?:n)? (?:creature|permanent|artifact|enchantment)",
]
_WIPE_PATTERNS = [
    r"destroy all",
    r"exile all",
    r"destroy each",
    r"all creatures get -\d+/-\d+",
    # Only a mass sacrifice counts: two or more, half, or the rest of the board.
    r"each (?:player|opponent) sacrifices (?:two|three|four|half|all|X)\b",
    r"return all (?:creatures|permanents|nonland permanents)",
    r"each creature deals damage",
    # Overload turns a targeted spell into a one-sided wipe, which is how
    # cards like Damn and Cyclonic Rift actually get played.
    r"\boverload \{",
]
_TUTOR_PATTERNS = [
    r"search your library for a card",
    r"search your library for an? (?:artifact|creature|enchantment|instant|sorcery|planeswalker) card",
]
_PROTECTION_PATTERNS = [
    r"gains? (?:hexproof|indestructible|protection|shroud)",
    r"gain (?:hexproof|indestructible|protection)",
    r"\bphases? out\b",
    r"sacrifice [^.]*: (?:regenerate|return)",
    r"prevent all damage",
    r"can't be (?:countered|targeted|destroyed)",
    r"you have (?:hexproof|shroud)",
    r"permanents you control gain",
]
_COUNTER_PATTERNS = [
    r"counter target",
    r"counter that spell",
]
_RECURSION_PATTERNS = [
    r"return target [^.]*from your graveyard",
    r"return [^.]*card from your graveyard to your hand",
    r"return [^.]*from your graveyard to the battlefield",
]
_GRAVEHATE_PATTERNS = [
    r"exile (?:all cards from )?(?:target player's|each player's|all) graveyard",
    r"exile target card from a graveyard",
    r"graveyards? can't",
    r"if a card would be put into a graveyard[^.]*exile",
]

_COMPILED = [
    (RAMP, [re.compile(p, re.I) for p in _RAMP_PATTERNS]),
    (DRAW, [re.compile(p, re.I) for p in _DRAW_PATTERNS]),
    (REMOVAL, [re.compile(p, re.I) for p in _REMOVAL_PATTERNS]),
    (WIPE, [re.compile(p, re.I) for p in _WIPE_PATTERNS]),
    (TUTOR, [re.compile(p, re.I) for p in _TUTOR_PATTERNS]),
    (PROTECTION, [re.compile(p, re.I) for p in _PROTECTION_PATTERNS]),
    (COUNTERSPELL, [re.compile(p, re.I) for p in _COUNTER_PATTERNS]),
    (RECURSION, [re.compile(p, re.I) for p in _RECURSION_PATTERNS]),
    (GRAVEHATE, [re.compile(p, re.I) for p in _GRAVEHATE_PATTERNS]),
]


def classify(card: CardEntry) -> list[str]:
    """Roles for one card. Lands only ever get the `land` role."""
    key = card.name.split("//")[0].strip().lower()
    if key in OVERRIDES:
        roles = list(OVERRIDES[key])
        if card.is_land and LAND not in roles:
            roles.append(LAND)
        return roles

    if card.is_land:
        # A land that tutors or draws still matters, but counting every fetch
        # land as ramp would drown the ramp count in what is really just mana.
        return [LAND]

    text = card.role_text()
    roles = [role for role, patterns in _COMPILED if any(p.search(text) for p in patterns)]

    # A board wipe is a strictly stronger statement than spot removal; keeping
    # both would double-count the same card in two budgets.
    if WIPE in roles and REMOVAL in roles:
        roles.remove(REMOVAL)

    if card.is_tutor and TUTOR not in roles:
        roles.append(TUTOR)
    return roles


def tag_deck(deck: Deck) -> None:
    """Attach roles to every card, in place."""
    for card in deck.cards:
        card.roles = classify(card)


def counts(deck: Deck) -> Counter:
    """Weighted role counts across the deck."""
    tally: Counter = Counter()
    for card in deck.cards:
        for role in card.roles:
            tally[role] += card.quantity
    return tally


def cards_with_role(deck: Deck, role: str) -> list[CardEntry]:
    return [c for c in deck.cards if role in c.roles]


def analyse(deck: Deck) -> dict:
    """Role counts plus a verdict against the normal EDH ranges."""
    tag_deck(deck)
    tally = counts(deck)

    findings = []
    for role, (low, high) in TARGETS.items():
        actual = tally.get(role, 0)
        if actual < low:
            findings.append(
                {
                    "role": role,
                    "count": actual,
                    "target": [low, high],
                    "verdict": "low",
                    "message": f"{actual} {role} — typical decks run {low}-{high}.",
                }
            )
        elif actual > high:
            findings.append(
                {
                    "role": role,
                    "count": actual,
                    "target": [low, high],
                    "verdict": "high",
                    "message": f"{actual} {role} — more than the usual {low}-{high}.",
                }
            )

    return {
        "counts": {role: tally.get(role, 0) for role in ROLE_ORDER},
        "targets": {k: list(v) for k, v in TARGETS.items()},
        "findings": findings,
        "by_role": {
            role: [c.name for c in cards_with_role(deck, role)] for role in ROLE_ORDER
        },
    }
