"""Functional card tags — what a card *does*, not what its text says.

Oracle-text regexes can see "Ayara draws a card". They cannot see that she is a
sacrifice outlet whose fuel this deck manufactures in bulk. Human-curated tags
can: Ayara is `sacrifice-outlet-creature`, `drain-life`, `draw-engine`.

Two sources serve the same Scryfall Tagger vocabulary:

- **Archidekt** ships `oracleCard.oTags` inside the deck payload we already
  download — 100% coverage on deck cards, no extra request. This is the
  baseline.
- **Scryfall Tagger** (`sources/tagger.py`) covers every card in Magic and
  carries the tag hierarchy, so it enriches deck cards and is the only way to
  find candidates *outside* the deck.

They differ only in formatting — Archidekt writes `sacrifice outlet-creature`,
Scryfall writes `sacrifice-outlet-creature` — so both are slugified to one key.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from .model import CardEntry, Deck
from .sources import tagger


def slugify(tag: str) -> str:
    """Normalise a tag from either source to one key."""
    text = unicodedata.normalize("NFKD", str(tag))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return text.strip("-")


# Tag data carries a lot of flavour trivia alongside the functional tags:
# `alliteration`, `single-english-word-name`, `bible-reference`, `cycle-*`.
# Rather than allowlist 4,500 tags, tags are matched into functional categories
# and anything matching none is kept on the card but ignored for engine work.
CATEGORIES: dict[str, tuple[str, ...]] = {
    "sacrifice": ("sacrifice-outlet", "free-sacrifice", "sacrifice-matters", "your-sacrifice"),
    "death-trigger": ("death-trigger", "dies-trigger", "leaves-battlefield-trigger"),
    "copy": ("copy", "clone", "copy-creature", "copy-nonland", "copy-permanent", "copy-spell", "changeling"),
    "tokens": (
        "creature-tokens", "token-generator", "populate", "repeatable-creature-tokens",
        # Anointed Procession makes no token of its own, but in Edgar's deck it
        # doubles every one — it is a token card, not an outsider.
        "token-doubler", "token-increaser",
    ),
    "drain": ("drain-life", "opponent-loses-life", "lifeloss", "group-slug", "aristocrat", "aristocrats"),
    "recursion": ("reanimate", "recursion", "return-from-graveyard", "graveyard-fuel", "persist"),
    "draw": ("draw", "draw-engine", "burst-draw", "repeatable-pure-draw", "card-advantage", "cantrip"),
    "removal": ("removal", "removal-destroy", "removal-exile", "spot-removal", "removal-bounce", "removal-toughness"),
    "sweeper": ("sweeper", "board-wipe", "mass-removal"),
    "ramp": ("ramp", "mana-rock", "mana-dork", "land-fetch", "treasures", "adds-multiple-mana"),
    "tutor": ("tutor",),
    "protection": ("protection", "hexproof", "indestructible", "counterspell", "ward"),
    "typal": ("typal", "tribal"),
    "counters": ("plus-one-counters", "proliferate", "gives-pp-counters"),
    "untap": ("untapper", "untap-permanent"),
    "wincon": ("win-the-game", "alternate-win", "overrun", "extra-combat"),
    # Static team pumps. A deck of creatures with no abilities has nothing
    # else to make them big, so for Jasmine these are the engine, not filler.
    "anthem": ("anthem", "power-boost-to-all"),
}

# A tag matching both is the narrower thing: mass-removal is a sweeper, and
# counting it as spot removal too would double-book the same card.
_EXCLUSIVE: tuple[tuple[str, str], ...] = (("sweeper", "removal"),)

# Tags that describe what a card *punishes*, not what it does. `hate-typal-human`
# is anti-tribal tech; counting it toward the typal cluster was exactly backwards.
ANTI_PREFIXES = ("hate-",)

# `typal-<x>` slugs that name a card class rather than an actual tribe.
_TYPAL_GENERIC = {
    "creature", "creatures", "choose", "non-choose", "instant", "sorcery",
    "artifact", "enchantment", "land", "planeswalker", "battle", "permanent",
}

# Tags that are about flavour, art or set structure rather than function.
NOISE_PREFIXES = ("cycle-", "synergy-")
NOISE_TAGS = {
    "alliteration",
    "single-english-word-name",
    "unique-type-line",
    "bible-reference",
    "fun-ruling",
    "namesake-spell",
    "virtual-legendary",
    "activated-ability",
    "triggered-ability",
    "impact-effect",
    "cheaper-than-mv",
    "noncreature-typal",
    "toll",
}


def is_functional(tag: str) -> bool:
    """Whether a tag says something about what the card does."""
    slug = slugify(tag)
    if slug in NOISE_TAGS or any(slug.startswith(p) for p in NOISE_PREFIXES):
        return False
    return bool(categories_for(slug))


def _pattern_matches(slug: str, pattern: str) -> bool:
    """Whether a pattern's hyphen-segments appear as a run of the slug's segments.

    Plain substring matching counted `trample` as ramp and `hate-typal-human`
    as typal. Matching on segment boundaries keeps `typal` matching `typal-ooze`
    without `ramp` matching the middle of an unrelated word.
    """
    return re.search(rf"(^|-){re.escape(pattern)}(-|$)", slug) is not None


def categories_for(tag: str) -> list[str]:
    """Functional categories a tag belongs to (often none)."""
    slug = slugify(tag)
    if slug in NOISE_TAGS or any(slug.startswith(p) for p in NOISE_PREFIXES):
        return []
    if any(slug.startswith(p) for p in ANTI_PREFIXES):
        return []
    found = [
        category
        for category, patterns in CATEGORIES.items()
        if any(_pattern_matches(slug, p) for p in patterns)
    ]
    for keep, drop in _EXCLUSIVE:
        if keep in found and drop in found:
            found.remove(drop)
    return found


def typal_subtypes(card: CardEntry) -> set[str]:
    """Tribes a card's `typal-<tribe>` tags name, generics filtered out."""
    tribes: set[str] = set()
    for tag in card.tags or []:
        slug = slugify(tag)
        if not slug.startswith("typal-"):
            continue
        tribe = slug[len("typal-"):]
        if tribe and tribe not in _TYPAL_GENERIC:
            tribes.add(tribe)
    return tribes


def functional_tags(card: CardEntry) -> list[str]:
    """The card's tags that carry functional meaning, slugified."""
    return sorted({slugify(t) for t in (card.tags or []) if is_functional(t)})


def card_categories(card: CardEntry) -> set[str]:
    """Functional categories this card belongs to."""
    found: set[str] = set()
    for tag in card.tags or []:
        found.update(categories_for(tag))
    return found


def deck_profile(deck: Deck, *, include_lands: bool = False) -> Counter:
    """How often each functional category appears across the deck.

    Lands are excluded by default: utility lands carry tags that would drown
    out what the spells are doing.
    """
    profile: Counter = Counter()
    for card in deck.cards:
        if card.is_land and not include_lands:
            continue
        for category in card_categories(card):
            profile[category] += card.quantity
    return profile


def tag_counts(deck: Deck) -> Counter:
    """Raw functional tag frequency — useful for naming a cluster precisely."""
    counts: Counter = Counter()
    for card in deck.cards:
        for tag in functional_tags(card):
            counts[tag] += card.quantity
    return counts


def ensure_tags(deck: Deck) -> int:
    """Fill in tags for cards that have none, from the local Tagger index.

    Archidekt supplies tags at import time, but a deck stored before tagging
    existed — or re-analysed without re-importing — would otherwise have none,
    and the engine would silently see an untagged bag of cards. Returns how
    many cards were backfilled.
    """
    filled = 0
    for card in deck.cards:
        if card.tags or not card.oracle_id:
            continue
        found = tagger.tags_for(card.oracle_id)
        if found:
            card.tags = sorted(found)
            filled += 1
    return filled
