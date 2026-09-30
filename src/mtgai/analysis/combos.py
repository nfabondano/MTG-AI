"""Find the combos a deck assembles, and the ones it nearly assembles.

The whole list goes to Commander Spellbook's find-my-combos in one request —
the same check commanderspellbook.com runs on a pasted list. Only when that is
unreachable does the tool fall back to probing the cards most likely to be in
a combo (commanders, Archidekt's combo-flagged cards, then EDHREC rank), and
it then says the result is partial.
"""

from __future__ import annotations

from ..model import Deck
from ..sources import spellbook

DEFAULT_PROBES = 25

# How many of each list the report shows. Complete combos are never cut from
# the data: the bracket estimate counts every one of them.
SHOWN = 15


def _probe_order(deck: Deck, limit: int) -> list[str]:
    """Pick which cards are worth asking about, most promising first."""
    commanders = [c for c in deck.cards if c.is_commander]
    others = [c for c in deck.cards if not c.is_commander and not c.is_basic_land]

    # Archidekt marks cards that appear in catalogued combos; those go first.
    # Then a low EDHREC rank means a widely played card, which is where the
    # rest of the catalogued combos live. Ordering by rank alone once left
    # Terror of the Peaks and its two partners outside the probe window and
    # reported a deck as combo-free that was not.
    others.sort(
        key=lambda c: (not c.combo_flagged, c.edhrec_rank is None, c.edhrec_rank or 10**9)
    )

    ordered = [c.name for c in commanders] + [c.name for c in others]
    return ordered[:limit]


def _playable(combo, identity: set[str]) -> bool:
    """Whether a combo is legal in this deck's colour identity.

    Without this the tool cheerfully suggests adding blue cards to a Mardu
    deck: plenty of catalogued combos pair a colourless staple the deck already
    runs, like Sol Ring, with a piece the deck could never cast. Suggesting an
    illegal card is the worst thing this tool could do, so the filter is
    applied to complete combos too. Spellbook writes colourless as "C", which
    is inside every identity.
    """
    colours = set(spellbook._normalise_identity(combo.identity))
    return colours <= identity


def _front(name: str) -> str:
    return name.split(" // ")[0]


def analyse(deck: Deck, *, probes: int = DEFAULT_PROBES) -> dict:
    commanders = [_front(c.name) for c in deck.cards if c.is_commander]
    main = [
        (_front(c.name), c.quantity)
        for c in deck.cards
        if not c.is_commander and not c.is_basic_land
    ]
    if not commanders and not main:
        return {"available": False, "complete": [], "near_miss": [], "probed": 0}

    method, partial, probed, failure = "find-my-combos", False, 0, ""
    try:
        found = spellbook.find_my_combos(commanders, main, deck.names())
    except Exception as exc:  # fall back to probing, and say so
        failure = str(exc)
        probe_cards = _probe_order(deck, probes)
        try:
            found = spellbook.find_in_deck(deck.names(), probe_cards, max_probes=probes)
        except Exception as exc2:  # the combo database is a nice-to-have
            return {
                "available": False,
                "reason": f"{failure}; probing also failed: {exc2}",
                "complete": [],
                "near_miss": [],
                "probed": 0,
            }
        method, partial, probed = "probe", True, len(probe_cards)

    identity = set(deck.color_identity())
    complete = [c for c in found["complete"] if _playable(c, identity)]
    near_miss = [c for c in found["near_miss"] if _playable(c, identity)]
    needs_template = [c for c in found.get("needs_template") or [] if _playable(c, identity)]

    if partial:
        note = (
            f"Commander Spellbook's full check was unavailable ({failure}), so only "
            f"the {probed} most combo-likely cards were checked — combos may be missing."
        )
    else:
        note = (
            "Every catalogued combo for the full list, from Commander Spellbook — "
            "only those inside your colour identity."
        )
    return {
        "available": True,
        "method": method,
        "partial": partial,
        "probed": probed,
        "complete": [c.to_dict() for c in complete],
        "near_miss": [{**c.to_dict(), "missing": c.missing} for c in near_miss[:SHOWN]],
        "needs_template": [
            {**c.to_dict(), "missing_template": c.missing_template}
            for c in needs_template[:SHOWN]
        ],
        "note": note,
    }
