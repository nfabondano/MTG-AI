"""Find the combos a deck assembles, and the ones it nearly assembles.

Querying Commander Spellbook for all 100 cards would be slow and impolite, so
the deck is probed with the cards most likely to be part of a combo: the
commanders first, then cards Archidekt has already flagged as appearing in
combos, then the rest by EDHREC rank as a proxy for "notable".
"""

from __future__ import annotations

from ..model import Deck
from ..sources import spellbook

DEFAULT_PROBES = 25


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
    applied to complete combos too.
    """
    return set(combo.identity or "") <= identity


def analyse(deck: Deck, *, probes: int = DEFAULT_PROBES) -> dict:
    probe_cards = _probe_order(deck, probes)
    if not probe_cards:
        return {"available": False, "complete": [], "near_miss": [], "probed": 0}

    try:
        found = spellbook.find_in_deck(deck.names(), probe_cards, max_probes=probes)
    except Exception as exc:  # the combo database is a nice-to-have
        return {
            "available": False,
            "reason": str(exc),
            "complete": [],
            "near_miss": [],
            "probed": 0,
        }

    identity = set(deck.color_identity())
    complete = [c for c in found["complete"] if _playable(c, identity)]
    near_miss = [c for c in found["near_miss"] if _playable(c, identity)]

    return {
        "available": True,
        "probed": len(probe_cards),
        "complete": [c.to_dict() for c in complete[:15]],
        "near_miss": [
            {**c.to_dict(), "missing": c.description.removeprefix("Missing: ")}
            for c in near_miss[:15]
        ],
        "note": (
            f"Checked the {len(probe_cards)} most combo-likely cards, not all 100, and "
            "kept only combos inside your colour identity."
        ),
    }
