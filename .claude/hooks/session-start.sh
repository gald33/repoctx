#!/usr/bin/env bash
# Claude Code on the web: ensure repoctx is ready WITHOUT blocking session init.
#
# Remote-only (no-op on local machines). SessionStart hooks run SYNCHRONOUSLY
# and block the session from finishing initialization until they return — and,
# unlike PostToolUse/Stop, SessionStart does NOT support the `{"async": true}`
# stdout directive (it is treated as context, not a control message). So this
# hook must always be fast and must never do heavy work inline:
#   - pip install + CPU torch + ~1.2GB model download (cold install), or
#   - an index (re)build, which loads the embedding model and can take minutes.
# Either one inline can exceed the hook timeout — or hang on the download — and
# leave the session stuck "initializing".
#
# All heavy work belongs in this environment's SETUP SCRIPT (the "Setup script"
# field in Claude Code on the web; the setup step for `claude --remote`). It
# runs once, before the session launches, and is cached:
#
#     bash scripts/cloud-setup.sh
#
# cloud-setup.sh installs the stack and builds the embedding index into the
# cached environment, so by the time this hook runs everything is already
# present and there is nothing left to do here.
set -euo pipefail

# Only relevant in Claude Code's remote (web) environment.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# Presence check only — no install, no index build, and no actual import of the
# heavy modules (find_spec locates them without loading torch, so this stays
# sub-second). If the setup script ran, the stack is present and we're done.
if python3 -c "import importlib.util as u, sys; sys.exit(0 if u.find_spec('repoctx') and u.find_spec('sentence_transformers') else 1)" 2>/dev/null; then
  exit 0
fi

# Stack missing → the environment setup script wasn't configured. Start the
# session anyway (repoctx degrades to lexical retrieval) and say how to enable
# semantic retrieval next time, instead of blocking init with an inline install.
echo "repoctx: embedding stack not installed — retrieval is lexical-only this" \
     "session. Set this environment's setup script to 'bash scripts/cloud-setup.sh'" \
     "so packages + model + index are prepared once (cached) BEFORE the session" \
     "starts, instead of blocking session initialization." 1>&2
exit 0
