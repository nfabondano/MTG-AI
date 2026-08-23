"""Estimate a deck's Commander bracket.

The bracket system sorts decks by what they are capable of, not by how much
they cost. The published criteria that a tool can actually measure are: how many
Game Changers the deck plays, whether it runs mass land denial, whether it
chains extra turns, how heavily it tutors, and whether it assembles two-card
infinite combos.

This produces an estimate to argue with, not a verdict. The last mile — how the
deck actually plays, and what the table expects — is a conversation, and the
report says so.
"""

from __future__ import annotations

from ..model import Deck

BRACKET_NAMES = {
    1: "Exhibition",
    2: "Core",
    3: "Upgraded",
    4: "Optimized",
    5: "cEDH",
}


def analyse(deck: Deck, combo_count: int = 0) -> dict:
    game_changers = [c.name for c in deck.cards if c.is_game_changer]
    extra_turns = [c.name for c in deck.cards if c.is_extra_turns]
    land_denial = [c.name for c in deck.cards if c.is_mass_land_denial]
    tutors = [c.name for c in deck.cards if "tutor" in (c.roles or []) or c.is_tutor]

    gc_count = len(game_changers)
    reasons: list[str] = []

    # Work upward from the most permissive bracket the deck still fits.
    if gc_count > 3:
        bracket = 4
        reasons.append(f"{gc_count} Game Changers (bracket 3 allows up to 3)")
    elif gc_count > 0:
        bracket = 3
        reasons.append(f"{gc_count} Game Changer{'s' if gc_count > 1 else ''}")
    else:
        bracket = 2
        reasons.append("no Game Changers")

    if land_denial:
        bracket = max(bracket, 4)
        reasons.append(f"mass land denial ({', '.join(land_denial)})")

    if len(extra_turns) > 1:
        bracket = max(bracket, 4)
        reasons.append(f"{len(extra_turns)} extra-turn effects")
    elif extra_turns:
        bracket = max(bracket, 3)
        reasons.append("an extra-turn effect")

    if combo_count:
        bracket = max(bracket, 3)
        reasons.append(f"{combo_count} two-card combo{'s' if combo_count > 1 else ''} available")

    if len(tutors) >= 8:
        bracket = max(bracket, 4)
        reasons.append(f"{len(tutors)} tutors — consistent enough to find combos reliably")

    # Bracket 1 is a deliberately gentle deck; a low-power list with no
    # interaction to speak of only qualifies if it also does none of the above.
    if bracket == 2 and not tutors and not combo_count and len(game_changers) == 0:
        wipes = sum(c.quantity for c in deck.cards if "wipe" in (c.roles or []))
        if wipes == 0:
            bracket = 1
            reasons.append("no tutors, combos or board wipes")

    declared = deck.archidekt_bracket
    mismatch = None
    if declared and declared != bracket:
        mismatch = (
            f"Archidekt has this deck marked as bracket {declared}; "
            f"the card pool looks more like {bracket}."
        )

    return {
        "estimate": bracket,
        "name": BRACKET_NAMES[bracket],
        "declared": declared,
        "mismatch": mismatch,
        "reasons": reasons,
        "game_changers": game_changers,
        "extra_turns": extra_turns,
        "mass_land_denial": land_denial,
        "tutors": tutors,
        "note": (
            "An estimate from what the deck can do on paper. How it actually plays, "
            "and what your table expects, is the real answer."
        ),
    }
