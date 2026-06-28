#!/bin/sh
# SessionStart hook: guarantee repoctx is importable before the MCP server
# spawns. Runs during init but BEFORE MCP startup, so a cold cloud VM ends up
# with a working repoctx server instead of a failed-to-spawn one.
#
# No-op when repoctx already imports (local dev with a populated .venv, or a
# warm cloud environment cache) — so it only does real work on a cold clone.
# Installs the light base only (no torch); the optional [embeddings] extra is
# left to the environment setup script, and repoctx degrades to lexical
# retrieval without it.
DIR="${CLAUDE_PROJECT_DIR:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}"
PY="$DIR/scripts/repoctx-py.sh"

if "$PY" -c "import repoctx" 2>/dev/null; then
  exit 0
fi

"$PY" -m pip install -e "$DIR" >/dev/null 2>&1 || true
exit 0
