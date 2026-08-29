---
description: Load a deck's context so you can ask questions about it
argument-hint: <deck slug, id, or part of its name>
allowed-tools: Bash(uv run mtg:*), Read, Grep
---

Load the deck `$ARGUMENTS` so we can talk about it.

1. Run `uv run mtg deck show "$ARGUMENTS"` to get the summary and the file paths.
2. Read that deck's `analysis.md`.
3. Read its `intent.md` if it exists — that's what I've declared the deck to be
   about, and it outranks any inference or statistic.
4. Read its `notes.md` — that's where my own thinking lives, and it should
   inform your answers.

Then give me a short orientation: what the deck is, and what the analysis
currently flags. Keep it to a handful of lines.

After that, stay in this deck's context and answer whatever I ask. Useful moves
while we talk:

- `uv run mtg deck cards "$ARGUMENTS" --role ramp` to list cards by role
  (ramp, draw, removal, wipe, tutor, protection, counterspell, recursion, land)
- `uv run mtg deck cards "$ARGUMENTS" --type Creature` to filter by type
- `uv run mtg card "<name>"` to look up any card
- Grep the deck's `deck.json` for specific card details

Don't edit anything in Archidekt — I apply changes there myself. If we land on
changes worth keeping, offer to append them to `notes.md`. If I explain what
the deck is *for* and it isn't captured yet, offer `/deck-intent` so the tool
remembers.
