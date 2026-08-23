"""Compare a deck against what EDHREC says other people build.

Three questions worth answering:

- Which cards do most decks with this commander play that you don't?
- Which cards have unusually high *synergy* with this commander that you're
  missing? (High synergy means the card shows up far more often with this
  commander than with decks of the same colours — it is the commander-specific
  signal, as opposed to generically good cards.)
- Which of your cards almost nobody else plays?

That last list is deliberately framed as "worth a second look", not "cut these".
Low inclusion often means a pet card, a budget choice, or a local metagame call,
and all three are legitimate reasons to ignore the statistics.
"""

from __future__ import annotations

from ..model import Deck
from ..sources import edhrec

# A card in at least this share of decks for the commander counts as a staple.
STAPLE_INCLUSION = 0.35
# Synergy above this is a strongly commander-specific card.
HIGH_SYNERGY = 0.20
# Below this share, a card is an unusual inclusion.
OFF_META_INCLUSION = 0.04


def analyse(deck: Deck, *, limit: int = 15) -> dict:
    commander_names = [c.name for c in deck.commanders]
    data = edhrec.commander_for(commander_names)

    if not data.found:
        return {
            "available": False,
            "reason": data.error or "no EDHREC data",
            "commander": commander_names,
            "missing_staples": [],
            "missing_synergy": [],
            "off_meta": [],
        }

    recommendations = data.by_name()
    in_deck = deck.names()
    identity = set(deck.color_identity())

    missing_staples = []
    missing_synergy = []
    for key, rec in recommendations.items():
        if key in in_deck:
            continue
        entry = rec.to_dict()
        if rec.inclusion >= STAPLE_INCLUSION:
            missing_staples.append(entry)
        if rec.synergy >= HIGH_SYNERGY:
            missing_synergy.append(entry)

    missing_staples.sort(key=lambda e: -e["inclusion"])
    missing_synergy.sort(key=lambda e: -e["synergy"])

    # Cards in the deck that the wider meta rarely plays.
    off_meta = []
    for card in deck.cards:
        if card.is_commander or card.is_basic_land:
            continue
        # Lands are skipped entirely. EDHREC's commander page lists roughly 150
        # spells and does not enumerate mana bases, so every dual and utility
        # land reads as "unusual" — which would have this tool advising you to
        # cut Plateau.
        if card.is_land:
            continue
        key = card.name.split("//")[0].strip().lower()
        rec = recommendations.get(key)
        if rec is None:
            # Absent from every list EDHREC returned — it did not make the cut
            # anywhere, which is a weaker signal than a measured low inclusion.
            off_meta.append(
                {
                    "name": card.name,
                    "inclusion": None,
                    "mana_value": card.mana_value,
                    "reason": "not on any EDHREC list for this commander",
                }
            )
        elif rec.inclusion < OFF_META_INCLUSION:
            off_meta.append(
                {
                    "name": card.name,
                    "inclusion": round(rec.inclusion, 4),
                    "mana_value": card.mana_value,
                    "reason": f"played in {rec.inclusion:.1%} of decks",
                }
            )

    # Surface the priciest unusual cards first — those are the ones where a
    # swap actually changes something.
    off_meta.sort(key=lambda e: -(e["mana_value"] or 0))

    return {
        "available": True,
        "commander": commander_names,
        "slug": data.slug,
        "identity": sorted(identity),
        "sample_size": max(
            (r.potential_decks for r in data.recommendations), default=0
        ),
        "missing_staples": missing_staples[:limit],
        "missing_synergy": missing_synergy[:limit],
        "off_meta": off_meta[:limit],
    }
