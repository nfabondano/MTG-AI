---
description: Get a deck down to 100 — which cards go, plus a few spares
argument-hint: <deck slug> [--to N] [--extra K] [--max-bracket N]
allowed-tools: Bash(uv run mtg:*), Read
---

The deck `$ARGUMENTS` has to come down to size. Ask the tool:

```bash
uv run mtg deck trim $ARGUMENTS
```

(Keep any `--to`, `--extra` or `--max-bracket` I passed. When I say how many
spares I want — "unas 2-4 extra" — use `--extra`. When I name a bracket, use
`--max-bracket N`; otherwise the tool takes it from intent.md, then Archidekt.)

Before you answer, read the deck's `intent.md` and `engine.md` if they exist.

Then give me the list, short enough for a phone:

- The cuts, numbered, each with its reason in a few words
- The spares, in order, for when I'd rather keep one of the cuts
- Anything the bracket requires, said plainly as required, not suggested

Each pick has a `tier`. The strong ones are illegal, bracket, declared,
castability, oversupply, curve and orphan: pass those on as they are. Picks
labelled "judgement call" mean the tool found nothing stronger — say so, and
if you'd pick differently, say which card and why, with a deck-internal reason.
Popularity is never a reason on its own; when it only broke a tie, the tool
says so as a weak signal.

Never offer the commander, a land, a card I declared untouchable, a combo piece
or a card named in my win conditions — the tool already refuses them, so if
one shows up, that's a bug worth reporting. If the land note says the deck
runs more lands than it needs, mention that cutting a land is an option.

I make the changes in Archidekt myself; give me exact card names.
