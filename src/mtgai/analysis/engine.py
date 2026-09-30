"""What the deck is actually trying to do, and which cards do it.

The tool used to model a deck as a bag of independent cards judged against
EDHREC averages. That is how it came to flag an entire aristocrats engine —
Ayara, Yawgmoth, Pitiless Plunderer, Species Specialist — as "unusual
inclusions" whose only crime was sitting outside a truncated popularity list.

This module replaces popularity with three deck-internal signals, all of which
survive on a commander nobody has built yet:

- **Engine clusters.** Functional tags reveal the archetype directly: a deck
  with 15 sacrifice outlets, 15 copy effects and 9 death triggers is a
  clone-aristocrats deck, whatever EDHREC has heard of.
- **Castability strain.** Which specific cards demand more coloured mana than
  the deck can reliably produce. This is the signal that actually explains a
  good cut, and the tool had the data all along without connecting it to a
  card.
- **Cluster oversupply.** A deck does not need seventeen ramp spells. A card in
  an overstuffed cluster is redundant in a way that has nothing to do with
  whether strangers play it.
"""

from __future__ import annotations

import re
from collections import Counter

from .. import tags as tagmod
from ..model import COLOR_NAMES, COLORS, CardEntry, Deck
from .mana import SOURCES_FOR_PIPS, _sources_by_color

# A cluster needs this many cards before it counts as part of the plan rather
# than incidental overlap.
CLUSTER_THRESHOLD = 5

# Roughly how many cards a deck wants in each category. Above the upper bound
# is redundancy worth spending on something else.
CATEGORY_TARGETS: dict[str, tuple[int, int]] = {
    "ramp": (8, 12),
    "draw": (8, 12),
    "removal": (6, 10),
    "sweeper": (2, 4),
    "sacrifice": (4, 8),
    "tutor": (0, 4),
    "protection": (2, 6),
    "recursion": (2, 6),
}

# What a commander's own tags imply the deck should value. This is the piece
# that encodes "understand the idea of the deck": Uugguu is `death-trigger` +
# `copy` + `tokens` + `typal`, so sacrifice outlets and clones are core to it,
# not filler.
COMMANDER_WANTS: dict[str, tuple[str, ...]] = {
    "death-trigger": ("sacrifice", "death-trigger", "drain", "recursion"),
    "sacrifice": ("sacrifice", "death-trigger", "drain", "tokens"),
    "tokens": ("tokens", "sacrifice", "drain"),
    "copy": ("copy", "tokens"),
    "drain": ("drain", "sacrifice", "tokens"),
    "typal": ("typal", "copy"),
    "recursion": ("recursion", "sacrifice", "death-trigger"),
    "counters": ("counters",),
    "untap": ("untap", "ramp"),
    # Niv-Mizzet, Ghost Counsel draws off every lifegain event: the 99 is
    # lifegain triggers and the payoffs that turn life into damage.
    "lifegain": ("lifegain", "drain"),
    # Cloud, Ex-SOLDIER: Equipment, whatever makes a body big enough to matter,
    # and protection for the creature wearing it all.
    "equipment": ("equipment", "pump", "protection"),
    "aura": ("aura", "pump", "protection"),
}

# When a commander arrives sparsely tagged — new sets ship before taggers catch
# up — its oracle text still says what it is about.
_ORACLE_WANTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bdies\b|\bdie\b|\bwhen(ever)? .* is put into a graveyard", re.I), "death-trigger"),
    (re.compile(r"\bsacrifice\b", re.I), "sacrifice"),
    # Creature tokens only — "create two Treasure tokens" is ramp, not a
    # tokens deck (it made Cloud want sacrifice outlets).
    (re.compile(r"\bcreate\b[^.]*\bcreature tokens?\b", re.I), "tokens"),
    (re.compile(r"\bcop(y|ies)\b", re.I), "copy"),
    (re.compile(r"\bdraws? (a|two|three|x) cards?\b", re.I), "draw"),
    (re.compile(r"loses? \d+ life|loses? life|drain", re.I), "drain"),
    (re.compile(r"\+1/\+1 counter", re.I), "counters"),
    # Not bare "lifelink": plenty of commanders have it without caring about
    # life gained.
    (re.compile(r"whenever you gain life|life you(?:'ve)? gained", re.I), "lifegain"),
    (re.compile(r"\bequip(?:ped)?\b|\bequipment\b", re.I), "equipment"),
    (re.compile(r"\bauras?\b", re.I), "aura"),
)

# A tribe only counts as the deck's tribe when the deck actually commits to it.
TRIBE_MIN = 4

# Below this many sources in one of its colours, a three-colour cost is a real
# strain — the same bar a two-pip cost has to clear.
THREE_COLOUR_SOURCES = SOURCES_FOR_PIPS[2]
SOURCES_SUFFICE = " — sources look sufficient"

# "If you control a commander, you may cast this spell without paying its mana
# cost" — Deadly Rollick, Flawless Maneuver. Their mana value says 3 or 4;
# with the commander out they cost nothing, so they are the last thing to cut
# for being expensive.
_FREE_WITH_COMMANDER = re.compile(
    r"if you control a commander,? you may cast (?:this spell|it) without paying its mana cost",
    re.I,
)

# Spells that discount themselves by the board — Blasphemous Act, Excalibur,
# Ghalta, delve and convoke. The printed mana value is their cost on an empty
# board; in the decks that run them they cost a few mana, so they are costed
# as three-drops. Reading the printed 9 or 12 made them "the most expensive
# card" in every ranking.
_SELF_DISCOUNT = re.compile(
    r"this spell costs \{[\dX]+\} less to cast,? (?:for each|where)|"
    r"\b(?:affinity for|convoke|improvise|delve)\b",
    re.I,
)
SELF_DISCOUNT_COST = 3.0

# Tags of cards that multiply what the deck already does. They are the reason
# the cluster works, so they are the last members of a cluster to go.
_MULTIPLIER_TAGS = frozenset(
    {
        "counter-increaser", "counter-doubler", "token-doubler", "token-increaser",
        "trigger-doubler", "life-doubler", "lifegain-increaser", "power-doubler",
        # March of the World Ooze makes every creature an Ooze: in a typal
        # deck that multiplies the tribe.
        "universal-type-change",
    }
)

# A cluster the commander's own text asks for gets to run deeper before it is
# called oversupplied — for a death-trigger commander, fourteen sacrifice
# outlets are the deck working, not bloat.
CORE_TARGET_MULTIPLIER = 2


def category_bounds(
    category: str, wants, overrides: dict[str, int] | None = None
) -> tuple[int, int]:
    """(floor, bound) for a category in this deck.

    When the commander asks for the job, its range starts where the generic
    one ends: the bound doubles, and the floor rises to the generic ceiling.
    A Voltron commander that wants protection is not "covered" by the
    generic two protection spells — that floor offered Clever Concealment
    and Heroic Intervention from Cloud's deck as spares. (Doubling the floor
    instead asked Cloud for sixteen ramp cards.) A declared target in
    intent.md replaces the bound, and the floor never exceeds it.
    """
    low, high = CATEGORY_TARGETS[category]
    if overrides and category in overrides:
        bound = overrides[category]
        return min(low, bound), bound
    if category in set(wants):
        return high, high * CORE_TARGET_MULTIPLIER
    return low, high


def commander_base_wants(deck: Deck) -> set[str]:
    """What the commander's own tags and text are about, before expansion.

    The expansion in COMMANDER_WANTS is for "what else does such a deck
    value"; this is "what does the commander itself do". The difference
    matters: Niv-Mizzet drains, and drain expands to sacrifice, but Niv asks
    for lifegain triggers, not for sacrifice outlets.
    """
    base: set[str] = set()
    for commander in deck.commanders:
        base |= tagmod.card_categories(commander)
        for pattern, category in _ORACLE_WANTS:
            if pattern.search(commander.role_text()):
                base.add(category)
    return base


def commander_wants(deck: Deck) -> list[str]:
    """Categories the commander's own text implies the deck is built around."""
    wants: set[str] = set()
    for category in commander_base_wants(deck):
        wants.add(category)
        wants.update(COMMANDER_WANTS.get(category, ()))
    if commander_tribe(deck):
        wants.add("typal")
        wants.update(COMMANDER_WANTS.get("typal", ()))
    return sorted(wants)


# Commander design roles, after JoeyDH's framing: is the commander the setup
# or the payoff? A force multiplier, or one of a kind? The answer says what
# the 99 must supply — a payoff commander needs the 99 to provide the fuel and
# the triggers; an enabler needs payoffs; a standalone threat needs protection.
_ROLE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # `~` stands in for the commander's own name ("Whenever Cloud attacks").
    (re.compile(r"whenever (?:(?:another|a|one or more|you|this creature)\b|~).*\b(dies|die|enters|attacks?|cast|sacrifice|gain|lose|draw)", re.I | re.S), "payoff"),
    (re.compile(r"\{t\}[^:]*:.*\b(add|create|draw|search|return)\b", re.I | re.S), "enabler"),
    (re.compile(r"sacrifice (a|another|an) [^:]*:", re.I), "enabler"),
    (re.compile(r"\bcop(y|ies)\b|\bdouble\b|\btwice\b|\badditional\b", re.I), "force-multiplier"),
    (re.compile(r"creatures? you control (get|have)\s*\+", re.I), "force-multiplier"),
    (re.compile(r"(flying|menace|trample|can't be blocked|double strike).*(deals? combat damage|whenever [^.]* attacks)", re.I | re.S), "wincon"),
    (re.compile(r"(hexproof|ward|indestructible|protection from)", re.I), "standalone"),
)


_REMINDER_RE = re.compile(r"\([^)]*\)")


def _commander_text(commander: CardEntry) -> str:
    """Rules text with reminder text removed and the card's name as `~`.

    Reminder text describes a keyword, not the design: Felisa's "Mentor
    (Whenever this creature attacks…)" is not what makes her a payoff. And a
    commander refers to itself by name — "Whenever Cloud attacks" — which the
    role patterns read as `~`.
    """
    text = _REMINDER_RE.sub("", commander.role_text())
    full = commander.name.split("//")[0].strip()
    for name in {full, full.split(",")[0].strip()}:
        if len(name) >= 3:
            text = re.sub(re.escape(name), "~", text, flags=re.I)
    return text


def classify_commander(deck: Deck) -> dict:
    """The commander's design role(s) and what the 99 must therefore supply."""
    roles: list[str] = []
    for commander in deck.commanders:
        text = _commander_text(commander)
        for pattern, role in _ROLE_PATTERNS:
            if role not in roles and pattern.search(text):
                roles.append(role)
    if not roles:
        roles = ["glue"]

    # What the commander itself asks for picks the wording, not the expansion:
    # drain expands to sacrifice, but a lifegain commander wants no outlets.
    base = commander_base_wants(deck)
    tribe = commander_tribe(deck)
    # "worth copying" only when the commander itself copies — not when copy
    # merely arrives through the typal expansion.
    copies = any("copy" in tagmod.card_categories(c) for c in deck.commanders)
    supplies: list[str] = []
    if "payoff" in roles:
        themed = False
        if "death-trigger" in base or "sacrifice" in base:
            themed = True
            if tribe:
                fuel = f"nontoken {tribe}s" + (" worth copying" if copies else " the trigger counts")
            else:
                fuel = "creatures the trigger counts"
            supplies.append(f"fuel: {fuel}")
            supplies.append("triggers: sacrifice outlets and asymmetric wipes, so deaths happen on your terms")
            supplies.append("conversion: drain, draw and token payoffs that turn deaths into wins")
        if "counters" in base:
            themed = True
            supplies.append("counters: ways to put +1/+1 counters on your creatures, since the commander counts them")
        if "lifegain" in base:
            themed = True
            supplies.append("fuel: repeatable lifegain — many small triggers beat one big one")
            supplies.append("conversion: payoffs that turn life gained into cards, damage or counters")
        if "equipment" in base or "aura" in base:
            themed = True
            gear = "Equipment" if "equipment" in base else "Auras"
            supplies.append(f"fuel: {gear} worth carrying, and cheap ways to cast and attach them")
            supplies.append("bodies: creatures to carry them, so one removal spell does not end the plan")
        if not themed:
            supplies.append("fuel: cards that cause what the commander rewards")
            supplies.append("conversion: payoffs that turn the reward into wins")
    if "enabler" in roles:
        supplies.append("payoffs: cards that want what the commander produces")
    if "force-multiplier" in roles and "payoff" not in roles:
        supplies.append("things worth multiplying")
    if "standalone" in roles or "wincon" in roles:
        supplies.append("protection: the deck leans on the commander surviving")
    if roles == ["glue"]:
        supplies.append("a plan of its own — the commander supports rather than defines it")

    return {"roles": roles, "supplies": supplies, "tribe": tribe}


def commander_tribe(deck: Deck) -> str | None:
    """The tribe the commander cares about, if the deck actually plays it.

    Only two sources count, in order of how directly they speak: the
    commander's own `typal-<tribe>` tags, then creature types its rules text
    names — each cross-checked against what the deck really contains.

    The commander's *own* creature type is not evidence. It made Cloud a
    Human deck and Felisa a Vampire deck, which put "nontoken Humans" in the
    report and exempted every Human from cuts.
    """
    census = _subtype_census(deck)
    if not census:
        return None

    def confirmed(candidates: set[str]) -> str | None:
        present = [(census.get(c.lower(), 0), c) for c in candidates]
        present = [(count, name) for count, name in present if count >= TRIBE_MIN]
        if not present:
            return None
        return max(present)[1]

    for commander in deck.commanders:
        found = confirmed({t.capitalize() for t in tagmod.typal_subtypes(commander)})
        if found:
            return found

    for commander in deck.commanders:
        text = _REMINDER_RE.sub("", commander.role_text())
        mentioned = {w.strip(",.") for w in text.split()}
        found = confirmed({w for w in mentioned if w.lower() in census})
        if found:
            return found
    return None


def _subtype_census(deck: Deck) -> dict[str, int]:
    """How many creature cards carry each creature type.

    Only creature (and kindred) front faces count. Counting every subtype
    turned "Equipment" into Cloud's tribe, because his text names it and the
    deck is full of artifacts with that subtype.
    """
    census: Counter = Counter()
    for card in deck.cards:
        if card.is_land:
            continue
        front = card.type_line.split("//")[0].split("—")[0]
        if not any(t in front for t in ("Creature", "Kindred", "Tribal")):
            continue
        for subtype in card.subtypes:
            census[subtype.lower()] += card.quantity
    return dict(census)


def tribe_census(deck: Deck, tribe: str | None) -> dict:
    """Who is in the tribe — by type line, by changeling, and conditionally.

    Clones and copy effects become the tribe when they copy a member — a blue
    clone of an Ooze *is* an Ooze — so they are counted as conditional members
    rather than outsiders.
    """
    if not tribe:
        return {}
    key = tribe.lower()
    true_type = changelings = conditional = 0
    for card in deck.cards:
        if card.is_land:
            continue
        if key in {s.lower() for s in card.subtypes}:
            true_type += card.quantity
        elif card.is_changeling:
            changelings += card.quantity
        elif "copy" in tagmod.card_categories(card):
            conditional += card.quantity
    return {
        "tribe": tribe,
        "true_type": true_type,
        "changelings": changelings,
        "conditional_copies": conditional,
    }


def is_tribe_member(card: CardEntry, tribe: str | None) -> bool:
    """True membership only — clones are conditional and judged separately."""
    if not tribe or card.is_land:
        return False
    if card.is_changeling:
        return True
    return tribe.lower() in {s.lower() for s in card.subtypes}


def _tribe_word(tribe: str) -> re.Pattern[str]:
    stem = re.escape(tribe)
    plural = re.escape(tribe[:-1] + "ves") if tribe.lower().endswith("f") else f"{stem}(?:e?s)?"
    return re.compile(rf"\b(?:{stem}|{plural})\b", re.I)


def serves_tribe(card: CardEntry, tribe: str | None) -> bool:
    """A member of the tribe, or a card whose rules text names it.

    March of the World Ooze is an enchantment, but "creatures you control are
    Oozes" is typal work in the Ooze deck. Reading only type lines made it
    look like the least-connected piece in the deck.
    """
    if is_tribe_member(card, tribe):
        return True
    if not tribe or card.is_land:
        return False
    return _tribe_word(tribe).search(_REMINDER_RE.sub("", card.role_text())) is not None


def clusters(deck: Deck) -> dict[str, int]:
    """Functional categories the deck invests in, with their card counts.

    Ordered by size, then name, so a tie never depends on dictionary order —
    the top clusters name the archetype.
    """
    profile = tagmod.deck_profile(deck)
    return {
        category: count
        for category, count in sorted(profile.items(), key=lambda kv: (-kv[1], kv[0]))
        if count >= CLUSTER_THRESHOLD
    }


# The owner's own Archidekt category names are ground truth about what a card
# is *for* in this deck. Slugified name -> engine category.
OWNER_CATEGORY_MAP: dict[str, str] = {
    "sac-outlet": "sacrifice",
    "sac-outlets": "sacrifice",
    "sacrifice": "sacrifice",
    "sacrifice-outlets": "sacrifice",
    "clone": "copy",
    "clones": "copy",
    "copy": "copy",
    "copies": "copy",
    "drain": "drain",
    "aristocrats": "drain",
    "token": "tokens",
    "tokens": "tokens",
    "draw": "draw",
    "card-draw": "draw",
    "card-advantage": "draw",
    "ramp": "ramp",
    "mana": "ramp",
    "recursion": "recursion",
    "reanimation": "recursion",
    "removal": "removal",
    "interaction": "removal",
    "protection": "protection",
    "tutor": "tutor",
    "tutors": "tutor",
    "sweeper": "sweeper",
    "sweepers": "sweeper",
    "board-wipes": "sweeper",
    "wipes": "sweeper",
    "finisher": "wincon",
    "finishers": "wincon",
    "wincon": "wincon",
    "wincons": "wincon",
    "win-conditions": "wincon",
    "lifegain": "lifegain",
    "life-gain": "lifegain",
    "lifelink": "lifegain",
    "pump": "pump",
    "voltron": "pump",
    "equipment": "equipment",
    "equipments": "equipment",
    "aura": "aura",
    "auras": "aura",
    "counters": "counters",
    "1-1-counters": "counters",  # "+1/+1 Counters", slugified
    "proliferate": "counters",
    "untap": "untap",
    "burn": "drain",
}


def owner_categories(card: CardEntry) -> set[str]:
    """Engine categories the owner's own Archidekt categories map onto."""
    return {
        OWNER_CATEGORY_MAP[slug]
        for slug in (tagmod.slugify(c) for c in card.categories or [])
        if slug in OWNER_CATEGORY_MAP
    }


def membership(card: CardEntry) -> set[str]:
    """Every category a card belongs to: what its tags and type say it does,
    plus what the owner filed it under.

    Used for participation and for deciding what a card is *for*. Counts stay
    tag-based on purpose: owner categories are coarse, and folding them into
    counts would inflate every support cluster and manufacture oversupply.
    """
    return tagmod.card_categories(card) | owner_categories(card)


def tag_participation(deck: Deck, tribe: str | None = None) -> None:
    """Record on each card which of the deck's clusters it belongs to.

    Membership comes from functional tags, from the owner's own Archidekt
    categories, and — in a typal deck — from simply being the tribe: an Ooze in
    the Ooze deck participates by existing, whatever its text tags say. That is
    the rule whose absence once marked two Oozes as doing nothing.
    """
    if tribe is None:
        tribe = commander_tribe(deck)
    active = set(clusters(deck)) | set(commander_wants(deck))
    for card in deck.cards:
        if card.is_land:
            card.engine_participation = []
            continue
        member = membership(card)
        if "typal" in active and serves_tribe(card, tribe):
            member.add("typal")
        card.engine_participation = sorted(member & active)


# Archidekt's default categories name a card type, not a job.
_TYPE_CATEGORY_SLUGS = frozenset(
    {
        "creature", "creatures", "instant", "instants", "sorcery", "sorceries",
        "artifact", "artifacts", "enchantment", "enchantments", "planeswalker",
        "planeswalkers", "land", "lands", "battle", "battles", "commander",
        "maybeboard", "sideboard", "kindred", "tribal", "other",
    }
)


def _orphans(deck: Deck, tribe: str | None, wincon_names: set[str]) -> tuple[list[str], list[str]]:
    """Cards that do nothing the tool can name, and cards it knows nothing about.

    An orphan is a tagged card with no category at all — not an engine piece,
    not support (ramp, removal, a tutor…), not a member of the tribe, not
    filed by the owner under any job, and not a way to win. Anything with a
    job is not an orphan just because its job did not form a cluster: that
    rule offered Demonic Tutor and Lae'zel as cuts.

    A card with no tags at all is not an orphan either; there is no data to
    judge it on, and saying so beats guessing.
    """
    orphans: list[str] = []
    no_data: list[str] = []
    for card in deck.cards:
        if card.is_land or card.is_commander or card.engine_participation:
            continue
        if not card.tags:
            no_data.append(card.name)
            continue
        if membership(card) or card.is_changeling or serves_tribe(card, tribe):
            continue
        owner_jobs = {tagmod.slugify(c) for c in card.categories or []} - _TYPE_CATEGORY_SLUGS
        if owner_jobs or card.name in wincon_names:
            continue
        orphans.append(card.name)
    return orphans, no_data


def castability(deck: Deck) -> list[dict]:
    """Cards that ask for more coloured mana than the deck reliably produces.

    Two things make a card hard to cast: demanding many pips of one colour, and
    demanding several different colours at once. Both are measured against the
    deck's real source counts rather than a rule of thumb.

    A card is the `sole_driver` of a colour when nothing else in the deck asks
    for as many pips of it — cutting that one card relaxes the whole mana base,
    which is exactly why `{B}{B}{B}` Ayara was the right cut from a deck sitting
    one black source short.
    """
    identity = set(deck.color_identity())
    _, sources = _sources_by_color(deck)

    # The heaviest demand per colour, and how many cards make that demand.
    peak: dict[str, int] = {c: 0 for c in COLORS}
    demand_counts: dict[tuple[str, int], int] = {}
    for card in deck.cards:
        if card.is_land:
            continue
        for color, count in card.pips().items():
            if not count:
                continue
            peak[color] = max(peak[color], count)
            demand_counts[(color, count)] = demand_counts.get((color, count), 0) + 1

    findings: list[dict] = []
    for card in deck.cards:
        if card.is_land or card.is_commander:
            continue
        pips = {c: n for c, n in card.pips().items() if n}
        if not pips:
            continue

        worst_short = 0
        reasons: list[str] = []
        sole = False
        short_colors: dict[str, dict[str, int]] = {}

        for color, count in pips.items():
            needed = SOURCES_FOR_PIPS.get(min(count, 3), 0)
            have = sources.get(color, 0)
            short = needed - have
            if short > 0:
                worst_short = max(worst_short, short)
                short_colors[color] = {"needed": needed, "have": have, "short": short}
                reasons.append(
                    f"{count} {COLOR_NAMES.get(color, color)} pips wants ~{needed} "
                    f"sources, deck has {have}"
                )
                # Nothing else asks this much of the colour, so this card alone
                # is holding the requirement up.
                if count == peak[color] and demand_counts.get((color, count), 0) == 1:
                    sole = True

        distinct = len([c for c in pips if c in identity])
        # Three colours in one cost only strain a mana base that is thin in
        # one of them. Tifa's {1}{R}{G}{W} in a deck with 26/24/22 sources is
        # hard to cast in name only; The Mimeoplasm with 19 blue sources is not.
        weak = {
            c: sources.get(c, 0)
            for c in pips
            if c in identity and sources.get(c, 0) < THREE_COLOUR_SOURCES
        }
        three_colour_strain = distinct >= 3 and bool(weak)
        if three_colour_strain:
            thin = ", ".join(
                f"{COLOR_NAMES.get(c, c)} {n}" for c, n in sorted(weak.items())
            )
            reasons.append(
                f"needs {distinct} different colours in one cost, with thin sources ({thin})"
            )
        elif distinct >= 3:
            reasons.append(f"needs {distinct} different colours in one cost{SOURCES_SUFFICE}")

        if reasons:
            # Being the sole reason a colour requirement is high matters most:
            # cutting that card relaxes the whole mana base. A three-colour cost
            # on thin sources outranks being one source short of a two-pip card.
            severity = worst_short * 2
            if sole:
                severity += 10
            if three_colour_strain:
                severity += 6

            findings.append(
                {
                    "name": card.name,
                    "mana_cost": card.mana_cost,
                    "shortfall": worst_short,
                    "distinct_colors": distinct,
                    "three_colour_strain": three_colour_strain,
                    "sole_driver": sole,
                    "severity": severity,
                    "reasons": reasons,
                    "short_colors": short_colors,
                }
            )

    findings.sort(key=lambda f: -f["severity"])
    return findings


def cut_reasons(finding: dict) -> list[str]:
    """A castability finding's reasons that argue for a cut — without the
    informational "sources look sufficient" line, which belongs in a list of
    hard-to-cast cards but reads as a contradiction inside a cut reason."""
    return [r for r in finding.get("reasons") or [] if not r.endswith(SOURCES_SUFFICE)]


def effective_cost(card: CardEntry) -> float:
    """What the card really costs to cast: free-with-commander spells cost 0,
    spells that discount themselves by the board cost at most three."""
    text = card.role_text()
    if _FREE_WITH_COMMANDER.search(text):
        return 0.0
    cost = float(card.mana_value or 0)
    if _SELF_DISCOUNT.search(text):
        return min(cost, SELF_DISCOUNT_COST)
    return cost


def free_with_commander(card: CardEntry) -> bool:
    """Deadly Rollick, Flawless Maneuver: no mana at all with the commander out."""
    return bool(_FREE_WITH_COMMANDER.search(card.role_text()))


def last_resort(card: CardEntry) -> bool:
    """Cards every ranking puts last, and no list offers as a ready
    alternative: Game Changers, spell-lands, what multiplies the deck's own
    mechanic, and spells that are free with the commander out."""
    slugs = {tagmod.slugify(t) for t in card.tags or []}
    return (
        card.is_game_changer
        or card.is_modal_land
        or bool(slugs & _MULTIPLIER_TAGS)
        or free_with_commander(card)
    )


def core_clusters(found: dict[str, int], wants) -> list[str]:
    """The deck's engine: the three biggest clusters the commander asks for.

    Falls back to the three biggest clusters when the commander asks for none
    of them. `found` is already ordered by (count, name), so ties are stable.
    """
    wants = set(wants)
    wanted = [c for c in found if c in wants]
    return wanted[:3] or list(found)[:3]


def is_dedicated(card: CardEntry, category: str, core, tribe: str | None) -> bool:
    """Whether a card is in a category *only* for that category's sake.

    A card that also serves the deck's engine is doing double duty: Luminous
    Broodmoth is tagged protection, but in Felisa it is a death-trigger engine
    piece, and counting it as spare protection is how Teferi's Protection got
    offered as a cut.
    """
    if card.is_commander or card.is_land:
        return False
    if category not in tagmod.card_categories(card):
        return False
    if membership(card) & (set(core) - {category}):
        return False
    return not serves_tribe(card, tribe)


def _name_key(name: str) -> str:
    return name.split("//")[0].strip().lower()


def rank_cut_candidates(
    cards: list[CardEntry],
    *,
    flexible=frozenset(),
    inclusion: dict[str, float] | None = None,
) -> list[CardEntry]:
    """Order cut candidates, most cuttable first.

    Declared flexible cards lead. Game Changers, spell-lands and cards that
    multiply the deck's own mechanic trail. Among the rest the most expensive
    to cast goes first — by *effective* cost, so a spell that is free with the
    commander out is the last to go — then the most single-purpose. Measured
    EDHREC inclusion only breaks what is still tied; a card EDHREC has no data
    on sorts as if widely played, because absence is not evidence.
    """
    inclusion = inclusion or {}

    def key(card: CardEntry):
        name = _name_key(card.name)
        slugs = {tagmod.slugify(t) for t in card.tags or []}
        return (
            name not in flexible,
            card.is_game_changer,
            card.is_modal_land,
            bool(slugs & _MULTIPLIER_TAGS),
            -effective_cost(card),
            len(membership(card)),
            inclusion.get(name, 1.0),
            name,
        )

    return sorted(cards, key=key)


def oversupplied(
    deck: Deck,
    wants: set[str] | None = None,
    overrides: dict[str, int] | None = None,
    *,
    core: list[str] | None = None,
    tribe: str | None = None,
) -> list[dict]:
    """Clusters holding more cards than a deck normally needs.

    A category the commander's own text asks for is judged against a doubled
    bound: it is the deck's engine, and the generic target would flag the deck
    for doing the thing it is built to do. If even the doubled bound is passed,
    the finding says so honestly instead of pretending the engine is filler.

    A declared intent overrides both: `core_categories: {ramp: 20}` means
    Nicolas wants twenty, and twenty is the bound.

    The count is every card in the category, but what can actually be cut is
    only the *dedicated* members — cards serving no other part of the engine —
    and only down to the category's minimum:
    `cuttable = min(count − bound, dedicated − floor)`. Felisa's nine
    protection cards are two dedicated ones plus seven engine pieces that
    also protect, so there is nothing to trim.
    """
    if wants is None:
        wants = set(commander_wants(deck))
    if tribe is None:
        tribe = commander_tribe(deck)
    if core is None:
        core = core_clusters(clusters(deck), wants)
    overrides = overrides or {}
    profile = tagmod.deck_profile(deck)
    out = []
    for category in CATEGORY_TARGETS:
        is_core = category in wants or category in overrides
        floor, bound = category_bounds(category, wants, overrides)
        count = profile.get(category, 0)
        if count <= bound:
            continue
        dedicated = sorted(
            (c for c in cards_in_category(deck, category) if is_dedicated(c, category, core, tribe)),
            key=lambda c: c.name,
        )
        dedicated_count = sum(c.quantity for c in dedicated)
        out.append(
            {
                "category": category,
                "count": count,
                "target_low": floor,
                "target_high": bound,
                "excess": count - bound,
                "commander_core": is_core,
                "dedicated": dedicated_count,
                "shared": count - dedicated_count,
                "members_dedicated": [c.name for c in dedicated],
                "cuttable": max(0, min(count - bound, dedicated_count - floor)),
            }
        )
    out.sort(key=lambda e: (-e["cuttable"], -e["excess"], e["category"]))
    return out


def cards_in_category(deck: Deck, category: str) -> list[CardEntry]:
    return [
        c
        for c in deck.cards
        if not c.is_land and category in tagmod.card_categories(c)
    ]


# Below this, a card is merely a bit awkward rather than a problem. A one- or
# two-source shortfall shared by half the blue cards is a mana-base finding —
# the answer is more sources, not cutting six spells.
CUT_SEVERITY = 6


# Cards that end games say so in a few recognisable ways.
_WINCON_RE = re.compile(
    r"wins? the game|you win the game|loses the game|can't lose the game|"
    r"deals damage equal to|combat damage to a player, .* loses",
    re.I,
)


def win_condition_inventory(deck: Deck) -> list[dict]:
    """How this deck actually ends games — or the honest news that it doesn't.

    Three signals, strongest first: the card says so outright, the curated
    tags call it a finisher effect, or Nicolas's own Archidekt category does.
    """
    found: list[dict] = []
    for card in deck.cards:
        if card.is_land:
            continue
        why = ""
        if _WINCON_RE.search(card.role_text()):
            why = "says so in its text"
        elif "wincon" in tagmod.card_categories(card):
            why = "finisher effect"
        elif "wincon" in owner_categories(card):
            why = "your own category marks it the finisher"
        if why:
            found.append({"name": card.name, "why": why})
    return found


# EDHREC Quadrant Theory: a deck is measured in four game states. Which tag
# categories serve which state.
_QUADRANTS = {
    "developing": ("ramp", "draw"),
    "parity": ("removal", "sweeper"),
    "winning": ("wincon", "tokens"),
    "losing": ("protection", "recursion"),
}


def quadrant_coverage(deck: Deck) -> dict[str, dict]:
    """Card counts per game state, so a gap is visible before a game shows it."""
    profile = tagmod.deck_profile(deck)
    out: dict[str, dict] = {}
    for quadrant, categories in _QUADRANTS.items():
        counts = {c: profile.get(c, 0) for c in categories if profile.get(c, 0)}
        out[quadrant] = {"total": sum(counts.values()), "from": counts}
    return out


def mana_fixes(deck: Deck, demoted: list[dict]) -> list[dict]:
    """Turn castability strain on engine cards into mana-base advice.

    A shortfall shared by the deck's own payoffs means the mana base is wrong,
    not the payoffs — the answer is more sources of that colour, and the deck
    usually contains the lands worth swapping out.
    """
    by_color: dict[str, dict] = {}
    for finding in demoted:
        for color, info in (finding.get("short_colors") or {}).items():
            entry = by_color.setdefault(
                color,
                {
                    "color": color,
                    "color_name": COLOR_NAMES.get(color, color),
                    "have": info["have"],
                    "needed": info["needed"],
                    "delta": info["short"],
                    "driven_by": [],
                },
            )
            entry["needed"] = max(entry["needed"], info["needed"])
            entry["delta"] = max(entry["delta"], info["short"])
            entry["driven_by"].append(finding["name"])

    fixes = []
    fetches = re.compile(r"search your library", re.I)
    for entry in by_color.values():
        swaps = [
            land.name
            for land in sorted(
                deck.lands,
                key=lambda l: (bool(l.produces()), len(l.produces()), l.name),
            )
            if entry["color"] not in land.produces()
            and not land.is_basic_land
            # A fetch produces nothing itself but finds the colour just fine.
            and not fetches.search(land.oracle_text or "")
        ]
        entry["swap_candidates"] = swaps[:3]
        fixes.append(entry)
    fixes.sort(key=lambda e: -e["delta"])
    return fixes


def analyse(deck: Deck, intent=None) -> dict:
    """The deck's engine: what it is built around, and what strains it.

    A declared intent (`intent.md`) outranks inference wherever the two speak
    to the same thing: the tribe, the archetype's name, and how deep a cluster
    is allowed to run.
    """
    tagmod.ensure_tags(deck)
    tribe = (getattr(intent, "tribe", "") or None) or commander_tribe(deck)
    tag_participation(deck, tribe)

    found = clusters(deck)
    wants = commander_wants(deck)
    overrides: dict[str, int] = dict(getattr(intent, "core_categories", None) or {})
    wants = sorted(set(wants) | set(overrides))
    strain = castability(deck)

    # Name the archetype from the biggest clusters the commander cares about.
    # Every deck has a dozen incidental overlaps; the archetype is the top few.
    core = core_clusters(found, wants)
    excess = oversupplied(deck, set(wants), overrides, core=core, tribe=tribe)

    wincons = win_condition_inventory(deck)
    orphans, no_data = _orphans(deck, tribe, {w["name"] for w in wincons})

    # A hard-to-cast card that the deck is built around is a mana-base problem,
    # not a cut. Only three kinds of strain still argue for cutting the card
    # itself: it alone holds a colour requirement up, it wants three colours in
    # one cost from a thin mana base, or it is not part of the plan anyway.
    wanted_set = set(wants)
    core_names = {
        c.name
        for c in deck.cards
        if set(c.engine_participation) & wanted_set or serves_tribe(c, tribe)
    }
    cuts: list[dict] = []
    demoted: list[dict] = []
    for finding in strain:
        if finding["severity"] < CUT_SEVERITY:
            continue
        if (
            finding["sole_driver"]
            or finding.get("three_colour_strain")
            or finding["name"] not in core_names
        ):
            if finding["sole_driver"] and finding.get("short_colors"):
                worst = max(finding["short_colors"].items(), key=lambda kv: kv[1]["short"])
                finding["alternative"] = (
                    f"add {worst[1]['short']} {COLOR_NAMES.get(worst[0], worst[0])} "
                    f"source{'s' if worst[1]['short'] > 1 else ''} instead"
                )
            cuts.append(finding)
        else:
            demoted.append(finding)

    archetype_parts = list(core)
    if tribe and "typal" in archetype_parts:
        archetype_parts[archetype_parts.index("typal")] = f"{tribe} typal"
    inferred = " + ".join(archetype_parts) if archetype_parts else "no dominant theme"
    declared = getattr(intent, "archetype", "") or ""
    if declared and declared != inferred:
        archetype = f"{declared} (inferred: {inferred})"
    else:
        archetype = declared or inferred

    return {
        "commander": [c.name for c in deck.commanders],
        "commander_wants": wants,
        "commander_role": classify_commander(deck),
        "tribe": tribe,
        "tribe_census": tribe_census(deck, tribe),
        "clusters": found,
        # Every category's count, not only the clusters: role reconciliation
        # needs "2 sweepers" as much as "30 death triggers".
        "profile": dict(
            sorted(tagmod.deck_profile(deck).items(), key=lambda kv: (-kv[1], kv[0]))
        ),
        "core": core,
        "archetype": archetype,
        "castability": strain,
        "castability_cuts": cuts,
        "mana_fixes": mana_fixes(deck, demoted),
        "oversupplied": excess,
        "orphans": orphans,
        # Cards with no tags at all: the tool cannot judge them, so they are
        # listed as such and never offered as cuts.
        "no_data": no_data,
        "win_conditions": wincons,
        "quadrants": quadrant_coverage(deck),
        "tag_counts": dict(tagmod.tag_counts(deck).most_common(20)),
        "note": (
            "Clusters come from human-curated functional tags, not oracle-text "
            "guessing. A card outside every cluster is not automatically bad — it "
            "may be doing something the tags do not name."
        ),
    }
