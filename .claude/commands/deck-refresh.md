---
description: Re-pull a deck from Archidekt after editing it there
argument-hint: <deck slug, id, or part of its name>
allowed-tools: Bash(uv run mtg:*), Read
---

I've edited this deck in Archidekt. Pull the current version and re-analyse it:

```bash
uv run mtg deck refresh "$ARGUMENTS"
```

If it says the deck is gone (404), it was probably rebuilt under a new link.
The error lists the owner's decks that could be the new one:

- If one of them is clearly it (same name), run
  `uv run mtg deck refresh "$ARGUMENTS" --follow`.
- If I sent the new link, run `uv run mtg deck relink "$ARGUMENTS" <new url>`.
- Otherwise show me the candidates and ask. `uv run mtg deck find <owner>`
  lists all of that owner's public decks.

Relinking and renames carry my `notes.md`, `intent.md` and a hand-edited
`engine.md` into the new folder. If the output says it kept the old folder
because two files differ, tell me which ones.

Then tell me what changed compared to what the analysis said before — whether
the things it previously flagged are resolved, and whether anything new turned
up. `git diff` on the deck folder will show you exactly what moved.
