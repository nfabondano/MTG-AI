---
description: Compare two of my decks
argument-hint: <deck one> <deck two>
allowed-tools: Bash(uv run mtg:*), Read, Grep
---

Compare these two decks: $ARGUMENTS

For each, run `uv run mtg deck show` and read its `analysis.md`. Then compare
them on the things that actually differ in play:

- Speed and curve
- Interaction density
- Estimated bracket — these should be close if I want to play them at the same
  table
- Price

Finish with a plain answer to: if I'm bringing one to a pod, what's the
difference in what it does?
