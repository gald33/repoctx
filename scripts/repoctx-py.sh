#!/bin/sh
# Resolve the Python interpreter that has repoctx importable, then exec it with
# the given args. Prefers a project-local virtualenv (local dev) and falls back
# to the PATH python (cloud sessions / pip-installed environments) so the same
# .mcp.json and hooks work on every machine — no hardcoded abspaths.
DIR="${CLAUDE_PROJECT_DIR:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}"
if [ -x "$DIR/.venv/bin/python3" ]; then
  exec "$DIR/.venv/bin/python3" "$@"
fi
exec python3 "$@"
