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

If the command fails because the Scryfall cache is missing, run
`uv run mtg cache refresh` first and retry.
