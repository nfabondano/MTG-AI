#!/bin/bash
# Prepare a session to run the `mtg` CLI.
#
# Runs on every session start. Only does real work in remote sessions (Claude
# Code on the web and the mobile app), where the container is fresh; a local
# checkout already has its virtualenv.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}"

# Idempotent, and fast on a warm container.
uv sync --quiet

# Point the CLI and MCP server at this checkout regardless of working directory.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export MTGAI_ROOT=\"${CLAUDE_PROJECT_DIR:-$PWD}\"" >> "$CLAUDE_ENV_FILE"
fi

# Warm the Scryfall card cache. Only new imports and suggestions need it —
# asking questions about a deck already in decks/ works without it — so a
# failure here is not worth failing the session over.
uv run mtg cache refresh >/dev/null 2>&1 || \
  echo "note: Scryfall cache not warmed; run 'uv run mtg cache refresh' if needed" >&2

exit 0
