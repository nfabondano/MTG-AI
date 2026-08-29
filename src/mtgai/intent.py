"""The deck's idea, in Nicolas's words, in a file the tool obeys.

`intent.md` is the missing input the Uugguu failure exposed: the tool had tag
counts and popularity lists, but no way to be *told* what the deck is for. This
module gives that a durable home — front matter the code reads, prose the code
preserves — with the same ownership contract as `notes.md`: the tool creates it
once (via the intent commands) and never rewrites it behind Nicolas's back.

The front matter is a deliberately strict hand-parsed subset, not YAML, because
this project takes no dependency for four field shapes:

    key: scalar
    key:
      - list item
    key:
      subkey: 3

Anything unrecognised is kept verbatim through programmatic edits, and a
malformed line is a warning, never an error — a hand-edited file must not be
able to break analysis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = 1

# The commander-role vocabulary, after JoeyDH's framing: is the commander the
# setup or the payoff? A force multiplier or one of a kind?
COMMANDER_ROLES = (
    "payoff",
    "enabler",
    "force-multiplier",
    "standalone",
    "wincon",
    "glue",
)

_LIST_FIELDS = ("commander_role", "win_conditions", "core_cards", "flexible_cards")
_DICT_FIELDS = ("core_categories",)
_SCALAR_FIELDS = (
    "schema_version",
    "archetype",
    "tribe",
    "budget_per_card",
    "power_bracket",
    "meta_notes",
    "source",
    "updated_at",
)


@dataclass
class DeckIntent:
    """What Nicolas says the deck is about. Everything is optional."""

    schema_version: int = SCHEMA_VERSION
    archetype: str = ""
    tribe: str = ""
    commander_role: list[str] = field(default_factory=list)
    win_conditions: list[str] = field(default_factory=list)
    # Sacred: the tool must never suggest cutting these.
    core_cards: list[str] = field(default_factory=list)
    # The opposite: offered first when a cluster needs trimming.
    flexible_cards: list[str] = field(default_factory=list)
    # Category -> how many cards this deck deliberately wants (an absolute
    # ceiling that replaces the generic target).
    core_categories: dict[str, int] = field(default_factory=dict)
    budget_per_card: float | None = None
    power_bracket: int | None = None
    meta_notes: str = ""
    prose: str = ""
    source: str = "generated"
    updated_at: str = ""
    # Front-matter lines the parser did not recognise, preserved verbatim so a
    # programmatic edit never destroys a hand addition.
    unknown_lines: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (
            self.archetype
            or self.tribe
            or self.commander_role
            or self.win_conditions
            or self.core_cards
            or self.flexible_cards
            or self.core_categories
            or self.meta_notes
            or self.prose.strip()
        )

    def sacred_names(self) -> set[str]:
        """Core cards, lowercased and front-faced, for cut protection."""
        return {name.split("//")[0].strip().lower() for name in self.core_cards}

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "archetype": self.archetype,
            "tribe": self.tribe,
            "commander_role": self.commander_role,
            "win_conditions": self.win_conditions,
            "core_cards": self.core_cards,
            "flexible_cards": self.flexible_cards,
            "core_categories": self.core_categories,
            "budget_per_card": self.budget_per_card,
            "power_bracket": self.power_bracket,
            "meta_notes": self.meta_notes,
            "prose": self.prose,
            "source": self.source,
            "updated_at": self.updated_at,
        }


def parse_intent(text: str) -> tuple[DeckIntent, list[str]]:
    """Parse intent.md. Returns the intent and any warnings.

    Tolerant field by field: a line the parser cannot place is either kept
    verbatim (unknown key) or skipped with a warning (malformed) — never fatal.
    """
    intent = DeckIntent()
    warnings: list[str] = []
    lines = text.splitlines()

    if not lines or lines[0].strip() != "---":
        intent.prose = text
        if text.strip():
            warnings.append("no front matter found; treating the whole file as prose")
        return intent, warnings

    try:
        closing = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        intent.prose = text
        warnings.append("unclosed front matter; treating the whole file as prose")
        return intent, warnings

    front = lines[1:closing]
    intent.prose = "\n".join(lines[closing + 1 :]).lstrip("\n")

    current_key: str | None = None
    for raw in front:
        if not raw.strip():
            continue
        stripped = raw.strip()

        if raw.startswith((" ", "\t")) or stripped.startswith("- "):
            # Continuation of a list or dict field.
            if current_key in _LIST_FIELDS and stripped.startswith("- "):
                getattr(intent, current_key).append(stripped[2:].strip())
                continue
            if current_key in _DICT_FIELDS and ":" in stripped:
                sub, _, value = stripped.partition(":")
                try:
                    getattr(intent, current_key)[sub.strip()] = int(value.strip())
                except ValueError:
                    warnings.append(f"ignored non-numeric {current_key} entry: {stripped!r}")
                continue
            if current_key is None or current_key in _SCALAR_FIELDS:
                warnings.append(f"ignored stray continuation line: {stripped!r}")
                continue
            warnings.append(f"ignored malformed line under {current_key}: {stripped!r}")
            continue

        if ":" not in stripped:
            warnings.append(f"ignored malformed front-matter line: {stripped!r}")
            current_key = None
            continue

        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()

        if key in _LIST_FIELDS or key in _DICT_FIELDS:
            current_key = key
            if value:
                if key in _LIST_FIELDS:
                    getattr(intent, key).extend(
                        v.strip() for v in value.split(",") if v.strip()
                    )
                else:
                    warnings.append(f"ignored inline value for {key}: {value!r}")
            continue

        current_key = None
        if key not in _SCALAR_FIELDS:
            intent.unknown_lines.append(raw)
            continue

        if key == "schema_version":
            try:
                intent.schema_version = int(value)
            except ValueError:
                warnings.append(f"ignored non-numeric schema_version: {value!r}")
        elif key == "budget_per_card":
            try:
                intent.budget_per_card = float(value) if value else None
            except ValueError:
                warnings.append(f"ignored non-numeric budget_per_card: {value!r}")
        elif key == "power_bracket":
            try:
                intent.power_bracket = int(value) if value else None
            except ValueError:
                warnings.append(f"ignored non-numeric power_bracket: {value!r}")
        else:
            setattr(intent, key, value)

    return intent, warnings


def render_intent(intent: DeckIntent) -> str:
    """Re-emit intent.md: known keys in fixed order, unknown lines verbatim."""
    out = ["---"]
    out.append(f"schema_version: {intent.schema_version}")
    if intent.archetype:
        out.append(f"archetype: {intent.archetype}")
    if intent.tribe:
        out.append(f"tribe: {intent.tribe}")
    for key in ("commander_role", "win_conditions", "core_cards", "flexible_cards"):
        values = getattr(intent, key)
        if values:
            out.append(f"{key}:")
            out.extend(f"  - {v}" for v in values)
    if intent.core_categories:
        out.append("core_categories:")
        out.extend(f"  {k}: {v}" for k, v in intent.core_categories.items())
    if intent.budget_per_card is not None:
        budget = intent.budget_per_card
        out.append(f"budget_per_card: {budget:g}")
    if intent.power_bracket is not None:
        out.append(f"power_bracket: {intent.power_bracket}")
    if intent.meta_notes:
        out.append(f"meta_notes: {intent.meta_notes}")
    if intent.source:
        out.append(f"source: {intent.source}")
    if intent.updated_at:
        out.append(f"updated_at: {intent.updated_at}")
    out.extend(intent.unknown_lines)
    out.append("---")
    prose = intent.prose.strip("\n")
    if prose:
        out.append("")
        out.append(prose)
    return "\n".join(out) + "\n"


def apply_assignments(
    intent: DeckIntent, assignments: dict[str, str]
) -> tuple[DeckIntent, list[str]]:
    """Apply `key=value` edits from the CLI. Returns warnings, never raises.

    List fields take a comma-separated replacement, or `+Name` / `-Name` to add
    or remove one entry. Dict fields take `category=N` pairs separated by
    commas. An empty value clears the field.
    """
    warnings: list[str] = []
    for key, value in assignments.items():
        value = value.strip()
        if key in _LIST_FIELDS:
            current = getattr(intent, key)
            if value.startswith("+"):
                name = value[1:].strip()
                if name and name.lower() not in {v.lower() for v in current}:
                    current.append(name)
            elif value.startswith("-"):
                name = value[1:].strip().lower()
                setattr(intent, key, [v for v in current if v.lower() != name])
            elif not value:
                setattr(intent, key, [])
            else:
                setattr(intent, key, [v.strip() for v in value.split(",") if v.strip()])
        elif key in _DICT_FIELDS:
            if not value:
                setattr(intent, key, {})
                continue
            parsed: dict[str, int] = dict(getattr(intent, key))
            for pair in value.split(","):
                sub, sep, num = pair.partition("=")
                if not sep:
                    sub, sep, num = pair.partition(":")
                try:
                    parsed[sub.strip()] = int(num.strip())
                except ValueError:
                    warnings.append(f"ignored non-numeric {key} entry: {pair.strip()!r}")
            setattr(intent, key, parsed)
        elif key == "prose":
            intent.prose = value
        elif key in _SCALAR_FIELDS:
            if key == "budget_per_card":
                try:
                    intent.budget_per_card = float(value) if value else None
                except ValueError:
                    warnings.append(f"ignored non-numeric budget_per_card: {value!r}")
            elif key == "power_bracket":
                try:
                    intent.power_bracket = int(value) if value else None
                except ValueError:
                    warnings.append(f"ignored non-numeric power_bracket: {value!r}")
            elif key == "schema_version":
                try:
                    intent.schema_version = int(value)
                except ValueError:
                    warnings.append(f"ignored non-numeric schema_version: {value!r}")
            else:
                setattr(intent, key, value)
        else:
            warnings.append(f"unknown intent field: {key!r}")
    return intent, warnings
