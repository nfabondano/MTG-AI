# MTG-AI

A workbench for analysing Magic: The Gathering Commander decks that live on
Archidekt. Decks are imported into `decks/`, analysed against Scryfall, EDHREC
and Commander Spellbook, and discussed here. **Nothing is ever written back to
Archidekt** — Nicolas applies changes there by hand.

## The workflow

1. Send an Archidekt deck link → `/deck-add <url>`
2. That creates `decks/<slug>/` with the list and a full analysis
3. Ask questions about it → `/deck <slug>`
4. Get concrete swaps → `/deck-suggest <slug>`
5. Nicolas edits the deck on Archidekt, then `/deck-refresh <slug>` re-syncs

## Slash commands

| Command | Use |
|---|---|
| `/deck-add <url>` | Import and analyse a new deck |
| `/deck <slug>` | Load a deck's context, then ask it anything |
| `/deck-list` | What's tracked |
| `/deck-suggest <slug>` | Cut/add suggestions, optionally `--budget N` |
| `/deck-refresh <slug>` | Re-pull after editing on Archidekt |
| `/deck-compare <a> <b>` | Compare two decks |

## The CLI is the engine

`uv run mtg …` does everything. There is also an MCP server (`.mcp.json`), but
a project MCP server is **not auto-approved in a freshly cloned repository**, so
in a cloud or phone session its tools may be unavailable. When they are, use
them; when they aren't, the CLI does the same work. Never tell Nicolas a thing
can't be done because MCP tools are missing — run the CLI.

```bash
uv run mtg deck add <url|id>          # import + analyse
uv run mtg deck list
uv run mtg deck show <ref>            # slim summary
uv run mtg deck cards <ref> --role ramp   # filter by role or --type
uv run mtg deck refresh <ref>         # re-pull from Archidekt
uv run mtg deck analyze <ref>         # regenerate analysis.md
uv run mtg deck suggest <ref> [--budget N] [--loose] [--max-bracket N]
uv run mtg deck engine <ref>          # what the deck is built around
uv run mtg card "<name>"              # Scryfall lookup
uv run mtg edhrec "<commander>"       # EDHREC recommendations
uv run mtg combos "<card>"            # Commander Spellbook
uv run mtg cache refresh              # local Scryfall cache
```

`<ref>` accepts a slug, an Archidekt id, or a fragment of the deck name. Add
`--json` to any command for structured output.

## Deck folders

```
decks/<slug>/
  source.json      raw Archidekt payload — never read this into context
  deck.json        normalised + enriched list; grep it, don't read it whole
  deck.txt         plain list, pasteable back into Archidekt
  analysis.md      the generated report — read this
  engine.md        what the deck is built around; Nicolas may edit it, and
                   his version wins — never overwrite an edited one
  suggestions.md   generated cut/add candidates, each with its evidence
  notes.md         Nicolas's own notes — never overwrite this file
```

**Context discipline matters here.** For a 100-card deck those files run roughly:

| File | Size | Cost |
|---|---:|---:|
| `source.json` | ~410 KB | ~100k tokens |
| `deck.json` | ~85 KB | ~21k tokens |
| `analysis.md` | ~7 KB | ~1.8k tokens |

Read `analysis.md` by default. Reach into `deck.json` with Grep or
`mtg deck cards` when you need specific cards, and never read it whole. There is
no reason to open `source.json` at all — everything in it is already in
`deck.json`.

**`notes.md` is off-limits for overwriting.** Append to it when Nicolas asks;
never rewrite it.

## What the analysis covers

Legality (100 cards, singleton, colour identity), mana (pip demand vs. sources
per colour, land count vs. curve), curve, role counts (ramp/draw/removal/wipes/
tutors/protection) against normal EDH ranges, EDHREC comparison, combos present
and one card away, an estimated Commander bracket, and price.

## Before you judge any card, read the engine

`decks/<slug>/engine.md` says what the deck is built around. Read it first.
Nicolas can edit it, and if he has, his version is authoritative — the tool
will not overwrite it.

These four rules exist because the tool once advised cutting an entire
aristocrats engine, and the reasoning behind it was nonsense:

- **Absence from an EDHREC list is not evidence.** The page returns a few
  hundred cards; a 100-card deck will always have entries outside it. Only a
  *measured* low inclusion means anything, and even then it is weak.
- **Never recommend a cut on popularity alone.** A cut needs a deck-internal
  reason: it is hard to cast, it sits in an oversupplied cluster, it is at the
  top of an already top-heavy curve, or it does nothing the deck is built
  around. If you cannot say which, do not suggest it.
- **Copy effects take on the copied card's characteristics.** A blue clone of a
  black commander *is a black creature*. Judge clones, changelings and tokens by
  what they become in this deck, never by what is printed on them. Reading the
  printed colour is exactly the mistake that produced the bad advice.
- **Check what the commander asks for.** A commander with a death trigger makes
  sacrifice outlets core, not filler. `mtg deck engine <ref>` spells this out.

## Judgement, not recitation

The analysis produces numbers. The useful part is what you make of them.

- **Role counts are heuristics.** They come from oracle-text pattern matching.
  Functional tags (`tags` on each card) are better — prefer them. If a card is
  miscategorised, say so rather than defending the number.
- **EDHREC describes the average deck, not a correct one.** A card almost
  nobody plays is often a deliberate choice — a pet card, a budget call, a local
  metagame. Flag it as worth discussing, never as an error.
- **Every suggested card must be inside the deck's colour identity.** If one
  isn't, that's a bug in this tool, not advice. Say so.
- **The bracket estimate is a conversation starter.** What a deck actually does
  at a table is not something a card list fully determines.
- Prefer a short verbal summary over pasting report sections back. The reports
  are on disk.

## Reading on a phone

Nicolas often uses this from the Claude mobile app. Keep answers short, avoid
wide tables, and lead with the thing worth acting on. `/model sonnet` or
`/model opus` switches models mid-session.

## Data sources

All public, unauthenticated, no scraping:

- **Archidekt** `archidekt.com/api/decks/{id}/` — deck lists and oracle data
- **Scryfall** — card data, prices, legality; bulk cache in `~/.cache/mtgai/`
- **EDHREC** `json.edhrec.com/pages/…` — meta statistics
- **Commander Spellbook** — combo database

Three of these have no official API contract. `src/mtgai/http.py` throttles and
caches every request; keep it that way.

## Working on this codebase

- `src/mtgai/service.py` holds the operations. The CLI and MCP server both call
  it — add new capability there, not in one front end.
- `uv run pytest` runs offline against recorded fixtures in `tests/fixtures/`.
- Functional tags come from Archidekt's `oTags` (free, in the payload) and the
  Scryfall Tagger bulk file (`mtg cache refresh`), which also supplies the tag
  hierarchy — `sacrifice-outlet` has 12 cards directly but 1,540 once children
  roll up, so never query a parent tag without the rollup.
- Two Archidekt quirks are load-bearing and covered by tests: a card is out of
  the deck when **any** of its categories is flagged `includedInDeck: false`
  (maybeboard cards also carry their type category), and `card.uid` is the
  Scryfall *printing* id while `card.oracleCard.uid` is the *oracle* id.
