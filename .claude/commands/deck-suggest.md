---
description: Get cut and add suggestions for a deck
argument-hint: <deck slug> [--budget N] [--max-bracket N]
allowed-tools: Bash(uv run mtg:*), Read
---

Generate suggestions for `$ARGUMENTS`:

```bash
uv run mtg deck suggest $ARGUMENTS
```

(If I passed a `--budget` or `--max-bracket` value, keep it on the command. When
I say the deck must stay at a bracket, use `--max-bracket N`: anything that
would raise it ends up under "Would raise the bracket" instead of as a swap.)

Then talk me through the results rather than dumping them:

- Which adds you'd actually make, and what you'd cut for each
- Which suggestions you'd ignore, and why — EDHREC's statistics don't know what
  my deck is trying to do, and an unusual card is often a deliberate choice
- Anything the suggestions miss that you noticed yourself

The tool already enforces `intent.md` where it exists: cards I declared sacred
never appear as cuts, and my category targets replace the generic ones. If the
suggestions still fight the deck's idea, that means the intent isn't captured —
offer `/deck-intent` rather than arguing with the output.

Every card you suggest must be inside the deck's colour identity. Say so if you
spot one that isn't — that's a bug worth reporting.

I apply changes in Archidekt by hand, so give me names I can search for.
