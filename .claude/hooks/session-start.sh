#!/usr/bin/env bash
# Claude Code on the web: prepare repoctx so the agent can use it this session.
#
# Remote-only (no-op on local machines) and idempotent. Runs SYNCHRONOUSLY: the
# session waits for setup to finish, which guarantees packages + index are ready
# before the agent starts, at the cost of a slower first session (cached after).
# To trade that guarantee for a faster start, switch to async by making the
# first line of stdout: echo '{"async": true, "asyncTimeout": 600000}'.
set -euo pipefail

# Only do the heavyweight setup in Claude Code's remote (web) environment.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

project_dir="${CLAUDE_PROJECT_DIR:-$(pwd)}"

# Send all setup output to stderr so verbose pip/index logs land in the hook log
# rather than the agent's SessionStart context. Best-effort: never block the
# session from starting if setup fails (the agent will see repoctx's loud
# lexical-fallback warnings and can re-run the build).
bash "$project_dir/scripts/cloud-setup.sh" "$project_dir" 1>&2 || true
exit 0
