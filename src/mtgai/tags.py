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
    # Creature tokens: fodder, bodies, going wide. The generic
    # `repeatable-token-generator` parent is left out because it also covers
    # Treasure makers, which turned Cloud's treasures into a sacrifice theme.
    "tokens": ("creature-tokens", "populate", "token-doubler", "token-increaser"),
    "drain": ("drain-life", "opponent-loses-life", "lifeloss", "group-slug", "aristocrat", "aristocrats"),
    "recursion": ("reanimate", "recursion", "return-from-graveyard", "graveyard-fuel", "persist"),
    "draw": ("draw", "draw-engine", "burst-draw", "repeatable-pure-draw", "card-advantage", "cantrip"),
    "removal": ("removal", "removal-destroy", "removal-exile", "spot-removal", "removal-bounce", "removal-toughness"),
    "sweeper": ("sweeper", "board-wipe", "mass-removal"),
    "ramp": ("ramp", "mana-rock", "mana-dork", "land-fetch", "treasures", "adds-multiple-mana"),
    "tutor": ("tutor",),
    "protection": ("protection", "hexproof", "indestructible", "counterspell", "ward"),
    "typal": ("typal", "tribal"),
    # `pp-counters` matches as a segment run, so it covers gains-, gives- and
    # repeatable-pp-counters and pp-counters-matter. The bare `counters-matter`
    # is left out: Archidekt ships it as the parent of energy, charge and
    # -1/-1 counter tags, which are other decks entirely.
    "counters": (
        "plus-one-counters", "proliferate", "pp-counters", "counter-increaser",
        "counter-doubler", "move-counters",
    ),
    "untap": ("untapper", "untap-permanent"),
    "wincon": ("win-the-game", "alternate-win", "overrun", "extra-combat"),
    # A Niv-Mizzet, Ghost Counsel deck is built on many small lifegain
    # triggers. Without this category Soul Warden and friends read as doing
    # nothing and were offered as cuts.
    "lifegain": (
        "lifegain", "repeatable-lifegain", "lifegain-matters", "gives-lifelink",
        "gains-lifelink", "lifegain-increaser", "lifegain-to-damage",
        "lifelink-counter", "synergy-lifelink",
    ),
    # Most Equipment carry no equipment tag at all — the type line says it —
    # so card_categories() adds this category from the type as well.
    "equipment": (
        "synergy-equipment", "quick-equip", "quick-attach", "auto-equip",
        "cost-reducer-equip-ability", "cost-reducer-equipment",
        "tutor-artifact-equipment", "alternate-equip-cost", "vanilla-equipment",
        "french-vanilla-equipment", "copy-equipment", "reanimate-equipment",
        "regrowth-equipment", "pseudo-equipment", "equipless-equipment",
    ),
    "aura": (
        "synergy-aura", "tutor-enchantment-aura", "vanilla-aura",
        "french-vanilla-aura", "reanimate-aura", "regrowth-aura", "copy-aura",
    ),
    # Making one creature big, or all of them. `power-matters` is deliberately
    # absent: it covers ~1,600 cards that merely care about power.
    "pump": ("power-boost-to-all", "anthem", "enlarge", "power-doubler", "gives-double-strike"),
}

# Categories every deck needs whatever it is built around. A card in one of
# these has a job even when no cluster of its kind forms.
SUPPORT = frozenset(
    {"ramp", "draw", "removal", "sweeper", "protection", "tutor", "recursion", "wincon"}
)

# A tag matching both is the narrower thing: mass-removal is a sweeper, and
# counting it as spot removal too would double-book the same card.
_EXCLUSIVE: tuple[tuple[str, str], ...] = (("sweeper", "removal"),)

# Tags that describe what a card *punishes*, not what it does. `hate-typal-human`
# is anti-tribal tech; counting it toward the typal cluster was exactly backwards.
# Matched on any segment: `draw-hate` (Smothering Tithe) punishes drawing, it
# does not draw.
ANTI_SEGMENTS = ("hate",)

# Tags that match a category's pattern but mean something else. `force-draw`
# makes someone else draw (the gift on Dawn's Truce); `sweeper-graveyard`
# exiles graveyards; `removal-equipment` destroys Equipment.
_TAG_EXCLUDE: dict[str, tuple[str, ...]] = {
    "draw": ("force-draw", "draw-matters"),
    "sweeper": ("sweeper-graveyard", "counterspell-sweeper"),
    "counters": ("counter-fuel", "remove-counters", "mm-counters"),
    "equipment": ("removal-equipment", "theft-equipment"),
    "aura": ("removal-aura", "theft-aura"),
    "pump": ("keyword-anthem", "prowess-anthem"),
}

# Archidekt ships a card's parent tags alongside the specific ones, so a
# child that means "not really this" has to cancel what the bare parent would
# grant — card by card. Swords to Plowshares carries `lifegain` *and*
# `opponent-lifegain`; Farseek carries `tutor` *and* `tutor-land`. Each entry
# is category -> (parent tags, cancelling children).
_PARENT_CANCELLED_BY: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "lifegain": (("lifegain",), ("opponent-lifegain",)),
    "tutor": (
        ("tutor", "tutor-to", "tutor-to-hand", "tutor-to-battlefield", "tutor-to-top",
         "tutor-to-graveyard"),
        ("tutor-land",),
    ),
}

# `typal-<x>` slugs that name a card class rather than an actual tribe.
_TYPAL_GENERIC = {
    "creature", "creatures", "choose", "non-choose", "instant", "sorcery",
    "artifact", "enchantment", "land", "planeswalker", "battle", "permanent",
}

# Tags that are about flavour, art or set structure rather than function.
# Most `synergy-*` tags are colour-pie trivia ("synergy-white"), but a few name
# exactly what an engine runs on, so those are let through.
NOISE_PREFIXES = ("cycle-", "synergy-")
_KEEP_SYNERGY = frozenset({"synergy-equipment", "synergy-aura", "synergy-lifelink"})
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


def _is_noise(slug: str) -> bool:
    if slug in _KEEP_SYNERGY:
        return False
    return slug in NOISE_TAGS or any(slug.startswith(p) for p in NOISE_PREFIXES)


def _is_anti(slug: str) -> bool:
    return any(segment in ANTI_SEGMENTS for segment in slug.split("-"))


def is_functional(tag: str) -> bool:
    """Whether a tag says something about what the card does."""
    slug = slugify(tag)
    if _is_noise(slug):
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
    if _is_noise(slug) or _is_anti(slug):
        return []
    found = [
        category
        for category, patterns in CATEGORIES.items()
        if any(_pattern_matches(slug, p) for p in patterns)
        and not any(_pattern_matches(slug, x) for x in _TAG_EXCLUDE.get(category, ()))
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
    """Functional categories this card belongs to: its tags, then its type.

    Two card-level corrections run on top of the per-tag mapping. A child tag
    that means "not really this" cancels what its bare parent alone would
    grant (a land tutor is ramp, not a tutor). And the type line fills in what
    tags leave unsaid: an Equipment is equipment even when Tagger never says so.
    """
    slugs = {slugify(t) for t in card.tags or []}
    by_tag = {slug: categories_for(slug) for slug in slugs}
    found: set[str] = {c for cats in by_tag.values() for c in cats}

    for category, (parents, cancellers) in _PARENT_CANCELLED_BY.items():
        if category not in found:
            continue
        cancelled = {s for s in slugs if any(_pattern_matches(s, c) for c in cancellers)}
        if not cancelled:
            continue
        # Keep the category only if a tag other than the bare parents and the
        # cancelling children still grants it on its own.
        if not any(
            category in cats and slug not in parents and slug not in cancelled
            for slug, cats in by_tag.items()
        ):
            found.discard(category)

    found |= _type_categories(card, found)
    return found


def _type_categories(card: CardEntry, from_tags: set[str]) -> set[str]:
    """Categories the front face's type line implies."""
    subtypes = card.subtypes
    found: set[str] = set()
    if "Equipment" in subtypes:
        found.add("equipment")
    # An Aura used as removal (Pacifism) is removal, not a voltron piece.
    if "Aura" in subtypes and "removal" not in from_tags:
        found.add("aura")
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
