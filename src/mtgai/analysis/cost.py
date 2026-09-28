"""What a card costs in this deck, which is not always what is printed on it.

The printed mana value is how Archidekt and every curve chart count a card, and
the histogram keeps counting that way. But judging a card as "the top of the
curve" on its printed cost is how The Great Henge and Blasphemous Act — both of
which discount themselves — came to be offered as "9 mana" cuts from a Slinza
deck, where every Beast also costs two less than it says.

Two corrections, both read from rules text:

- **Self-discounting spells** ("This spell costs {X} less to cast…", affinity,
  undaunted) have no fixed real cost, so they are never judged on the printed
  one. Convoke, delve and improvise are not discounts — they change what pays
  the cost, not what the cost is — so they are left alone.
- **The commander's own discount** ("Beast spells you cast cost {2} less to
  cast") applies to every card it names. The commander is the one card the
  deck can count on, and the deck is built on the assumption it is there.
  Mana the commander makes only for certain spells pays for them the same
  way: Jasmine Boreal of the Seven's {G}{W} is for creatures with no
  abilities, so a 6-mana vanilla costs her deck 4.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..model import CardEntry, Deck

_SELF_DISCOUNT_RE = re.compile(r"\bthis spell costs\b[^.]*?\bless to cast\b", re.I)
# Keywords whose whole meaning is "this spell costs less" — their reminder text
# says so too, but not every data source keeps reminder text.
_SELF_DISCOUNT_KEYWORDS = {"affinity", "undaunted"}

# Only unconditional discounts: "Creature spells you cast with power 4 or
# greater cost {2} less" names a condition this tool cannot check, so it is
# left alone rather than guessed at.
_DISCOUNT_RE = re.compile(
    r"(?P<what>(?:[A-Za-z'-]+ ){0,2}[A-Za-z'-]+) spells you cast cost "
    r"\{(?P<amount>\d+)\} less to cast",
    re.I,
)

# "{T}: Add {G}{W}. Spend this mana only to cast creature spells with no
# abilities." — restricted mana, read as a discount for exactly those spells.
_RESTRICTED_MANA_RE = re.compile(
    r"\{T\}: Add (?P<mana>(?:\{[WUBRGC]\})+)\.\s*Spend this mana only to cast "
    r"(?P<what>(?:[A-Za-z'-]+ )*?)spells?(?P<vanilla> with no abilities)?\.",
    re.I,
)

_COLOR_WORDS = {"white": "W", "blue": "U", "black": "B", "red": "R", "green": "G"}
_CARD_TYPES = {
    "artifact", "battle", "creature", "enchantment", "instant", "kindred",
    "land", "legendary", "planeswalker", "snow", "sorcery", "tribal",
}


@dataclass(frozen=True)
class Discount:
    """A commander's standing cost reduction for the spells it names."""

    source: str
    what: str
    amount: int
    # Restricted mana ("Spend this mana only to cast…") rather than a cost
    # reduction, and whether it is only for creatures with no abilities.
    mana: bool = False
    no_abilities: bool = False

    @property
    def label(self) -> str:
        """The commander's short name — "Slinza" for "Slinza, the Spiked Stampede"."""
        return self.source.split(",")[0].strip()

    @property
    def phrase(self) -> str:
        """How a report names it: "Slinza's Beast discount", "Jasmine…'s mana"."""
        if self.mana:
            return f"{self.label}'s mana"
        return f"{self.label}'s {self.what} discount"

    def applies_to(self, card: CardEntry) -> bool:
        if card.is_commander or card.is_land:
            return False
        if self.no_abilities and not card.has_no_abilities:
            return False
        options = re.split(r",\s*|\s+(?:and|or)\s+", self.what.lower())
        return any(
            option.split() and all(_word_matches(w, card) for w in option.split())
            for option in options
        )


def _word_matches(word: str, card: CardEntry) -> bool:
    if word.startswith("non") and len(word) > 3:
        return not _word_matches(word[3:], card)
    if word in _COLOR_WORDS:
        return _COLOR_WORDS[word] in (card.colors or [])
    front = card.type_line.split("//")[0].replace("—", " ")
    if word in {w.lower() for w in front.split()}:
        return True
    # A changeling is every creature type, the named tribe included.
    return (
        word not in _CARD_TYPES
        and card.is_changeling
        and "creature" in front.lower()
    )


def commander_discounts(deck: Deck) -> list[Discount]:
    """Unconditional cost reductions the commanders' own text grants."""
    found: list[Discount] = []
    for commander in deck.commanders:
        text = commander.rules_text()
        for match in _DISCOUNT_RE.finditer(text):
            found.append(
                Discount(commander.name, match.group("what"), int(match.group("amount")))
            )
        for match in _RESTRICTED_MANA_RE.finditer(text):
            what = match.group("what").strip() or "creature"
            found.append(
                Discount(
                    commander.name,
                    what,
                    len(re.findall(r"\{[WUBRGC]\}", match.group("mana"))),
                    mana=True,
                    no_abilities=bool(match.group("vanilla")),
                )
            )
    return found


def self_discounting(card: CardEntry) -> bool:
    """True when the card's printed cost overstates what it usually costs."""
    if any(k.strip().lower() in _SELF_DISCOUNT_KEYWORDS for k in card.keywords or []):
        return True
    # Reminder text included on purpose: affinity's says "This spell costs {1}
    # less to cast for each…".
    return bool(_SELF_DISCOUNT_RE.search(card.role_text()))


def generic_mana(card: CardEntry) -> int:
    """The generic part of the front face's cost — all a discount can reduce."""
    front = (card.mana_cost or "").split("//")[0]
    return sum(int(n) for n in re.findall(r"\{(\d+)\}", front))


def effective_mana_value(card: CardEntry, discounts: list[Discount]) -> float:
    """Mana value once the commander's discounts apply; coloured pips never shrink."""
    reduction = sum(d.amount for d in discounts if d.applies_to(card))
    return max(0.0, (card.mana_value or 0) - min(reduction, generic_mana(card)))


def discount_for(card: CardEntry, discounts: list[Discount]) -> Discount | None:
    """The first commander discount that applies to this card, if any."""
    return next((d for d in discounts if d.applies_to(card)), None)
