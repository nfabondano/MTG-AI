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

    # Cards the wider meta measurably rarely plays.
    #
    # Absence from EDHREC's lists is NOT evidence and must never appear here.
    # The page returns a few hundred cards; a 100-card deck will always have
    # entries outside that. Treating absence as a signal is what made the tool
    # flag an entire aristocrats engine — Ayara, Yawgmoth, Pitiless Plunderer,
    # Species Specialist — as "unusual inclusions". Only a *measured* inclusion
    # rate counts, and only when EDHREC actually returned data for the card.
    off_meta = []
    covered = 0
    for card in deck.cards:
        if card.is_commander or card.is_basic_land:
            continue
        # Lands are skipped: EDHREC does not enumerate mana bases, so every dual
        # would read as unusual and the tool would advise cutting Plateau.
        if card.is_land:
            continue
        key = card.name.split("//")[0].strip().lower()
        rec = recommendations.get(key)
        if rec is None:
            continue
        covered += 1
        if rec.inclusion < OFF_META_INCLUSION:
            off_meta.append(
                {
                    "name": card.name,
                    "inclusion": round(rec.inclusion, 4),
                    "mana_value": card.mana_value,
                    "reason": f"played in {rec.inclusion:.1%} of decks with this commander",
                }
            )

    off_meta.sort(key=lambda e: e["inclusion"])

    # With thin coverage the comparison says more about EDHREC than the deck.
    spells = sum(1 for c in deck.cards if not c.is_land and not c.is_basic_land)
    coverage = covered / spells if spells else 0.0
    if coverage < 0.25:
        off_meta = []

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
        "covered_cards": covered,
        "coverage": round(coverage, 3),
    }
