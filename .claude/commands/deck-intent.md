---
description: Teach the tool what a deck is about — a short interview it then obeys
argument-hint: <deck slug, id, or part of its name>
allowed-tools: Bash(uv run mtg:*), Read
---

Interview me about what `$ARGUMENTS` is trying to do, and store the answers
where the tool obeys them.

1. Run `uv run mtg deck intent "$ARGUMENTS" --json`. The `inferred` block is
   the tool's guess: archetype, tribe, commander role, and what the 99 must
   supply. The `archidekt` block has anything I wrote on Archidekt.
2. Ask me **at most five short questions, one at a time**, each pre-filled with
   the inferred default so I can just say "yes":
   - "This looks like <inferred archetype>, with the commander as the
     <role> — right?"
   - "How does it win?"
   - "Which cards are untouchable? (I'll never suggest cutting those)"
   - "Budget per card, if any?"
   - "Anything about your table or meta I should know?"
   I'm usually on my phone — keep every question to a line or two, and skip
   questions the Archidekt description already answers.
3. Write my answers:

   ```bash
   uv run mtg deck intent "$ARGUMENTS" --set archetype="..." --set tribe="..."
   uv run mtg deck intent "$ARGUMENTS" --set win_conditions="..., ..."
   uv run mtg deck intent "$ARGUMENTS" --set core_cards="+Card Name"   # repeatable
   uv run mtg deck intent "$ARGUMENTS" --set budget_per_card=20 --set meta_notes="..."
   ```

   Use `core_categories` when I say things like "this deck deliberately runs
   20 ramp": `--set core_categories="ramp=20"`.
4. Re-run the analysis so everything downstream picks the intent up:

   ```bash
   uv run mtg deck analyze "$ARGUMENTS"
   ```

5. Confirm in one or two lines what changed: what's now sacred, what targets
   moved, what the declared archetype is.

The file behind this is `decks/<slug>/intent.md` — front matter the tool reads,
prose below it that's mine. I can edit it by hand any time; the tool never
overwrites it on refresh.
