---
description: Re-pull a deck from Archidekt after editing it there
argument-hint: <deck slug, id, or part of its name>
allowed-tools: Bash(uv run mtg:*), Read
---

I've edited this deck in Archidekt. Pull the current version and re-analyse it:

```bash
uv run mtg deck refresh "$ARGUMENTS"
```

Then tell me what changed compared to what the analysis said before — whether
the things it previously flagged are resolved, and whether anything new turned
up. `git diff` on the deck folder will show you exactly what moved.
