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

from .. import tags as tagmod
from ..model import Deck
from ..sources import edhrec
from . import engine as engine_mod

# A card in at least this share of decks for the commander counts as a staple.
STAPLE_INCLUSION = 0.35
# Synergy above this is a strongly commander-specific card.
HIGH_SYNERGY = 0.20
# Below this share, a card is an unusual inclusion.
OFF_META_INCLUSION = 0.04
# A theme needs at least this many decks before its page is a population.
THEME_MIN_DECKS = 20
# Below this many decks, every comparison is a small-sample caveat.
SMALL_SAMPLE = 50

# EDHREC cardlist headers that are mana base, not spells. Headlining these as
# "staples you're missing" buried the signal under Command Towers.
MANA_LISTS = {"Lands", "Utility Lands", "Mana Artifacts"}

# Theme slug -> the engine clusters that betray the deck is that build.
THEME_CLUSTERS: dict[str, tuple[str, ...]] = {
    "clones": ("copy",),
    "tokens": ("tokens",),
    "aristocrats": ("sacrifice", "drain"),
    "sacrifice": ("sacrifice",),
    "sacrifice-matters": ("sacrifice",),
    "lifegain": ("lifegain", "drain"),
    "equipment": ("equipment",),
    "voltron": ("equipment", "aura", "pump"),
    "auras": ("aura",),
    "reanimator": ("recursion",),
    "graveyard": ("recursion",),
    "counters": ("counters",),
    "plus-1-plus-1-counters": ("counters",),
}

# Tribe -> EDHREC plural slug, where naive +s is wrong.
_PLURALS = {"elf": "elves", "wolf": "wolves", "dwarf": "dwarves", "mouse": "mice"}


def tribe_slug(tribe: str) -> str:
    lower = tribe.lower()
    return _PLURALS.get(lower, f"{lower}s")


def detect_build_theme(deck: Deck, data: edhrec.CommanderData, intent=None) -> dict | None:
    """Which of this commander's build variants Nicolas's list actually is.

    Declared intent wins outright; otherwise the deck's own evidence decides —
    the tribe for a tribe-named theme, mapped cluster weight for the rest. A
    theme with too few decks is never a population worth comparing against.
    """
    profile = tagmod.deck_profile(deck)
    tribe = engine_mod.commander_tribe(deck)
    declared = ""
    if intent is not None:
        declared = f"{getattr(intent, 'archetype', '')} {getattr(intent, 'tribe', '')}".lower()

    # Evidence says which theme this deck is; the theme's own deck count says
    # how much a comparison against it can mean. 21 sacrifice cards should not
    # send the deck to a 25-deck population when 18 clones match a 115-deck
    # one, so evidence is weighted by the square root of the population.
    candidates: list[tuple[float, int, dict]] = []
    for theme in data.themes:
        if theme["count"] < THEME_MIN_DECKS:
            continue
        name, slug = theme["name"], theme["slug"]
        bonus = 0
        mentions = {name.lower(), name.lower().rstrip("s"), slug.replace("-", " ")}
        if declared and any(m and m in declared for m in mentions):
            bonus = 1000
        evidence = 0
        if tribe and slug.rstrip("s") == tribe.lower():
            evidence += profile.get("typal", 0)
        evidence += sum(profile.get(c, 0) for c in THEME_CLUSTERS.get(slug, ()))
        if not bonus and evidence < engine_mod.CLUSTER_THRESHOLD:
            continue
        score = (bonus + evidence) * theme["count"] ** 0.5
        candidates.append((score, theme["count"], theme))
    if not candidates:
        return None
    return max(candidates, key=lambda t: (t[0], t[1]))[2]


def analyse(deck: Deck, *, limit: int = 15, intent=None) -> dict:
    commander_names = [c.name for c in deck.commanders]
    data = edhrec.commander_for(commander_names)
    basis = "all-builds"
    basis_label = ""
    themes: list[dict] = []
    similar: list[str] = []

    if not data.found:
        # No page for this commander (typically: the card is brand new). The
        # tribe's format-wide tag page is a weaker but honest substitute.
        reason = data.error or "no EDHREC data"
        tribe = engine_mod.commander_tribe(deck)
        fallback = edhrec.tag_page(tribe_slug(tribe)) if tribe else None
        if fallback is None or not fallback.found:
            return {
                "available": False,
                "reason": reason,
                "commander": commander_names,
                "missing_staples": [],
                "missing_synergy": [],
                "off_meta": [],
            }
        data = fallback
        basis = "tribe-tag"
        basis_label = (
            f"no EDHREC page for this commander yet — compared against the "
            f"{tribe} theme across all decks, a weaker signal"
        )
    else:
        themes = data.themes
        similar = data.similar
        theme = detect_build_theme(deck, data, intent)
        if theme is not None:
            theme_data = edhrec.theme(data.slug, theme["slug"])
            if theme_data.found and theme_data.recommendations:
                theme_data.themes = themes
                theme_data.similar = similar
                data = theme_data
                basis = "theme"
                basis_label = (
                    f"vs {theme_data.num_decks or theme['count']} {theme['name']} "
                    f"builds of {commander_names[0]}"
                )

    recommendations = data.by_name()
    in_deck = deck.names()
    identity = set(deck.color_identity())

    missing_staples = []
    missing_mana_staples = []
    missing_synergy = []
    for key, rec in recommendations.items():
        if key in in_deck:
            continue
        entry = rec.to_dict()
        if rec.inclusion >= STAPLE_INCLUSION:
            if rec.list_name in MANA_LISTS:
                missing_mana_staples.append(entry)
            else:
                missing_staples.append(entry)
        if rec.synergy >= HIGH_SYNERGY and rec.list_name not in MANA_LISTS:
            missing_synergy.append(entry)

    missing_staples.sort(key=lambda e: -e["inclusion"])
    missing_mana_staples.sort(key=lambda e: -e["inclusion"])
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
    # Measured inclusion per card, for ordering candidates the deck's own
    # evidence already put on the table. Never a reason by itself.
    inclusion: dict[str, float] = {}
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
        inclusion[key] = round(rec.inclusion, 4)
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
        inclusion = {}

    sample_size = data.num_decks or max(
        (r.potential_decks for r in data.recommendations), default=0
    )
    small_sample = 0 < sample_size < SMALL_SAMPLE

    return {
        "available": True,
        "commander": commander_names,
        "slug": data.slug,
        "identity": sorted(identity),
        "basis": basis,
        "basis_label": basis_label,
        "themes": themes,
        "similar": similar,
        "sample_size": sample_size,
        "small_sample": small_sample,
        "missing_staples": missing_staples[:limit],
        "missing_mana_staples": missing_mana_staples[:limit],
        "missing_synergy": missing_synergy[:limit],
        "off_meta": off_meta[:limit],
        "inclusion": inclusion,
        "covered_cards": covered,
        "coverage": round(coverage, 3),
    }
