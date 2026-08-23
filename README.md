# MTG-AI

An Archidekt deck analysis workbench. Point it at an Archidekt deck, and it
pulls the list down, files it in a folder, and analyses it against Scryfall card
data, EDHREC meta statistics, and the Commander Spellbook combo database.

It never writes back to Archidekt. Suggestions are advisory — you apply the ones
you like by hand.

## Quick start

```bash
uv sync
uv run mtg cache refresh                                  # one-time, ~25 MB
uv run mtg deck add https://archidekt.com/decks/7000000/  # import + analyse
uv run mtg deck list
```

That creates `decks/<slug>/` holding the raw payload, a normalised decklist, a
plain-text list you can paste back into Archidekt, and generated analysis.

## Commands

| Command | What it does |
|---|---|
| `mtg deck add <url-or-id>` | Fetch, normalise, enrich and analyse a deck |
| `mtg deck list` | Show every tracked deck |
| `mtg deck show <slug>` | Slim summary of one deck |
| `mtg deck refresh <slug>` | Re-pull from Archidekt and re-analyse |
| `mtg deck analyze <slug>` | Regenerate `analysis.md` |
| `mtg deck suggest <slug>` | Regenerate `suggestions.md` |
| `mtg card <name>` | Look up a card |
| `mtg edhrec <commander>` | EDHREC summary for a commander |
| `mtg cache refresh` | Refresh the local Scryfall card cache |

Every command accepts `--json` for machine-readable output.

## What the analysis covers

- **Legality** — 100 cards, singleton, colour identity, Commander legality
- **Mana** — coloured pip demand vs. actual sources, per colour; land count vs. curve
- **Curve** — mana value distribution, average MV, type spread
- **Roles** — ramp, card draw, spot removal, board wipes, tutors, protection,
  each compared against normal EDH ranges
- **EDHREC** — staples and high-synergy cards you're missing, plus off-meta
  inclusions worth a second look
- **Combos** — combos the deck completes, and combos it is one card away from
- **Bracket** — an estimated Commander bracket from Game Changers, tutors, fast
  mana, extra turns and mass land denial
- **Price** — deck total and the most expensive cards

## Using it from your phone

Everything lives in the repo, so any Claude Code session on any device sees all
your decks and their analysis. Open the Claude app on this repository and:

```
/model sonnet
/deck-list
/deck <slug>
```

then ask whatever you want about the deck. See `CLAUDE.md` for the full
workflow.

## Data sources

All four are public and unauthenticated; no scraping or browser automation is
involved.

| Source | Used for |
|---|---|
| [Archidekt](https://archidekt.com) | Deck lists and per-card oracle data |
| [Scryfall](https://scryfall.com/docs/api) | Card data, prices, legality |
| [EDHREC](https://edhrec.com) | Commander meta statistics |
| [Commander Spellbook](https://commanderspellbook.com) | Combo database |

Archidekt, EDHREC and Commander Spellbook have no official API contract. This
tool caches aggressively and throttles every request to stay a good citizen.
