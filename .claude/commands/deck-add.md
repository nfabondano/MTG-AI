---
description: Import an Archidekt deck and analyse it
argument-hint: <archidekt url or deck id>
allowed-tools: Bash(uv run mtg:*), Read
---

Import the Archidekt deck `$ARGUMENTS` into this repository.

Run:

```bash
uv run mtg deck add "$ARGUMENTS"
```

Then read the generated `analysis.md` in the new deck folder and give me a short
verbal summary — the three or four things most worth acting on, in plain
language. Don't paste the whole report back; it's on disk and I can open it.

After the summary, offer the short intent interview (don't block on it): ask
whether the inferred archetype is right, how the deck wins, and which cards are
untouchable — then store the answers with
`uv run mtg deck intent <slug> --set key=value` and re-run
`uv run mtg deck analyze <slug>`. `/deck-intent <slug>` runs the full version
any time. If I decline, move on.

If the command fails because the Scryfall cache is missing, run
`uv run mtg cache refresh` first and retry.
