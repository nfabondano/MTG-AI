"""Estimate a deck's Commander bracket.

The bracket system sorts decks by what they are capable of, not by how much
they cost. The published criteria a tool can actually measure are how many
Game Changers the deck plays, whether it runs mass land denial, whether it
chains extra turns, and whether it assembles two-card infinite combos.

Tutors are not among them any more. WotC's October 21, 2025 update removed
tutor restrictions from every bracket and left the most efficient tutors to
the Game Changers list. The tool used to push a deck to bracket 4 for eight
tutors — narrow Equipment tutors included — which is how an Equipment deck
with no Game Changers and no combos read as "Optimized".

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

# Bracket 3 tolerates a few Game Changers; past this the deck is bracket 4.
GAME_CHANGER_LIMIT_B3 = 3

# Commander Spellbook rates every combo with a bracket tag. Exhibition and
# Core (and the retired "Precon Appropriate") are its judgement that a combo
# belongs at a low-bracket table — usually a big finite finisher rather than a
# true infinite. Oddball, Powerful and Spicy put a deck at bracket 3 at least;
# Ruthless (fast, efficient, game-ending) at bracket 4. Banned means the combo
# uses a banned card, which legality reports.
COMBO_TAG_FLOOR: dict[str, int | None] = {
    "E": None,
    "C": None,
    "PA": None,
    "O": 3,
    "P": 3,
    "S": 3,
    "R": 4,
    "B": None,
}
COMBO_TAG_NAMES = {
    "E": "exhibition",
    "C": "core",
    "PA": "precon-appropriate",
    "O": "oddball",
    "P": "powerful",
    "S": "spicy",
    "R": "ruthless",
    "B": "uses a banned card",
}


def target_for(deck: Deck, intent=None, explicit: int | None = None) -> tuple[int | None, str]:
    """The bracket the deck is meant to sit at, and who said so.

    An explicit request wins, then what Nicolas declared in intent.md, then the
    bracket set on Archidekt. Suggestions and trimming respect it by default,
    so re-running an analysis no longer forgets a cap `suggest` was given.
    """
    if explicit:
        return int(explicit), "requested"
    declared = getattr(intent, "power_bracket", None)
    if declared:
        return int(declared), "intent.md"
    if deck.archidekt_bracket:
        return int(deck.archidekt_bracket), "Archidekt"
    return None, ""


def combo_size(combo: dict) -> int:
    """Pieces in a combo, commander and generic pieces included."""
    return int(
        combo.get("size")
        or len(combo.get("cards") or []) + len(combo.get("requires") or [])
    )


def combo_floor(combo: dict) -> int | None:
    """The lowest bracket a deck can claim while it holds this combo.

    Mass land denial is bracket 4 whatever its size. Otherwise only a
    two-card combo moves the bracket — that is what the criteria restrict —
    and by how much is Spellbook's rating. An unrated two-card combo is read
    as bracket 3, never as safe.
    """
    if any("mass land denial" in p.lower() for p in combo.get("produces") or []):
        return 4
    if combo_size(combo) != 2:
        return None
    tag = combo.get("bracket_tag") or ""
    if tag in COMBO_TAG_FLOOR:
        return COMBO_TAG_FLOOR[tag]
    return 3


def combo_raises_bracket(combo: dict) -> bool:
    return combo_floor(combo) is not None


def _describe_combo(combo: dict) -> str:
    tag = COMBO_TAG_NAMES.get(combo.get("bracket_tag") or "", "unrated")
    return f"{' + '.join(combo.get('cards') or [])} ({tag})"


def analyse(
    deck: Deck,
    combos: list[dict] | None = None,
    *,
    target: int | None = None,
    target_source: str = "",
) -> dict:
    combos = combos or []
    raising = [c for c in combos if combo_raises_bracket(c)]
    two_card = [c for c in combos if combo_size(c) == 2]
    casual = [c for c in two_card if not combo_raises_bracket(c)]
    larger = [c for c in combos if combo_size(c) > 2 and not combo_raises_bracket(c)]
    combo_count = len(raising)

    game_changers = [c.name for c in deck.cards if c.is_game_changer]
    extra_turns = [c.name for c in deck.cards if c.is_extra_turns]
    land_denial = [c.name for c in deck.cards if c.is_mass_land_denial]
    # Informational only since October 2025. Lands that search (Axgard
    # Armory) are left out; they are utility lands, not tutors.
    tutors = [
        c.name for c in deck.cards
        if not c.is_land and ("tutor" in (c.roles or []) or c.is_tutor)
    ]

    gc_count = len(game_changers)
    reasons: list[str] = []

    # Work upward from the most permissive bracket the deck still fits.
    if gc_count > GAME_CHANGER_LIMIT_B3:
        bracket = 4
        reasons.append(
            f"{gc_count} Game Changers (bracket 3 allows up to {GAME_CHANGER_LIMIT_B3})"
        )
    elif gc_count > 0:
        bracket = 3
        reasons.append(f"{gc_count} Game Changer{'s' if gc_count > 1 else ''}")
    else:
        bracket = 2
        reasons.append("no Game Changers")

    if land_denial:
        bracket = max(bracket, 4)
        reasons.append(f"mass land denial ({', '.join(land_denial)})")

    # Brackets 1-3 forbid *chaining* extra turns. One extra-turn card on its
    # own is legal in a core deck; two or more is where chaining starts.
    if len(extra_turns) > 1:
        bracket = max(bracket, 4)
        reasons.append(f"{len(extra_turns)} extra-turn effects — chaining territory")

    if combo_count:
        floor = max(combo_floor(c) or 3 for c in raising)
        bracket = max(bracket, floor)
        reasons.append(
            f"{combo_count} two-card combo{'s' if combo_count > 1 else ''} in the deck: "
            + "; ".join(_describe_combo(c) for c in raising)
        )
    if casual:
        reasons.append(
            "two-card combos Spellbook rates as fine at a casual table, not counted: "
            + "; ".join(_describe_combo(c) for c in casual)
        )
    if larger:
        reasons.append(
            f"{len(larger)} combo{'s' if len(larger) > 1 else ''} of three or more pieces "
            "— the brackets restrict two-card combos only"
        )

    if tutors:
        reasons.append(
            f"{len(tutors)} tutor{'s' if len(tutors) > 1 else ''} — not a bracket "
            "criterion since WotC's October 2025 update; the efficient ones are "
            "Game Changers"
        )

    # Bracket 1 is a deliberately gentle deck; a low-power list with no
    # interaction to speak of only qualifies if it also does none of the above.
    if bracket == 2 and not combo_count and len(game_changers) == 0:
        wipes = sum(c.quantity for c in deck.cards if "wipe" in (c.roles or []))
        if wipes == 0:
            bracket = 1
            reasons.append("no Game Changers, combos or board wipes")

    declared = deck.archidekt_bracket
    mismatch = None
    if target and target_source == "intent.md" and target != bracket:
        mismatch = (
            f"intent.md declares bracket {target}; the card pool looks more like {bracket}."
        )
    elif declared and declared != bracket:
        mismatch = (
            f"Archidekt has this deck marked as bracket {declared}; "
            f"the card pool looks more like {bracket}."
        )

    return {
        "estimate": bracket,
        "name": BRACKET_NAMES[bracket],
        "declared": declared,
        "target": target,
        "target_source": target_source,
        "mismatch": mismatch,
        "reasons": reasons,
        "game_changers": game_changers,
        "extra_turns": extra_turns,
        "mass_land_denial": land_denial,
        "tutors": tutors,
        "combos": [c.get("cards") for c in raising],
        "casual_combos": [c.get("cards") for c in casual],
        "maybeboard": _maybeboard_watch(deck, bracket, gc_count, len(extra_turns)),
        "note": (
            "An estimate from what the deck can do on paper. How it actually plays, "
            "and what your table expects, is the real answer."
        ),
    }


def _maybeboard_watch(deck: Deck, bracket: int, gc_count: int, extra_turn_count: int) -> dict:
    """What the maybeboard would do to the bracket if it came in.

    The maybeboard is where a bracket-2 player parks the cards they are tempted
    by, so this is the most useful bracket question the tool can answer: which
    of those cards can be added freely, and which one move the deck up.
    """
    changers = [e["name"] for e in deck.excluded if e.get("is_game_changer")]
    land_denial = [e["name"] for e in deck.excluded if e.get("is_mass_land_denial")]
    extra_turns = [e["name"] for e in deck.excluded if e.get("is_extra_turns")]
    tutors = [e["name"] for e in deck.excluded if e.get("is_tutor")]

    warnings: list[str] = []
    if changers:
        if gc_count == 0:
            warnings.append(
                f"adding any one of {', '.join(changers)} makes this a bracket 3 deck"
            )
        room = GAME_CHANGER_LIMIT_B3 - gc_count
        if len(changers) > room:
            warnings.append(
                f"adding more than {room} of them makes it bracket 4"
                if room > 0
                else "the deck is already past the bracket 3 Game Changer limit"
            )
    if land_denial:
        warnings.append(
            f"{', '.join(land_denial)} is mass land denial — bracket 4 the moment it goes in"
        )
    if extra_turns and extra_turn_count + len(extra_turns) > 1:
        warnings.append(
            f"{', '.join(extra_turns)} would give the deck {extra_turn_count + len(extra_turns)} "
            "extra-turn effects — chaining, which is bracket 4"
        )

    return {
        "game_changers": changers,
        "mass_land_denial": land_denial,
        "extra_turns": extra_turns,
        "tutors": tutors,
        "warnings": warnings,
    }
