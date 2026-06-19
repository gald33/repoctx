#!/usr/bin/env bash
# Shared cloud-session setup for repoctx.
#
# Used by Claude Code's SessionStart hook (.claude/hooks/session-start.sh) and
# intended to be the command a Codex cloud environment's setup script runs too,
# so both tools share one setup path.
#
# A fresh cloud clone is missing three things repoctx needs; this installs them:
#   1) packages — the repoctx package + the [embeddings] extra
#                 (sentence-transformers / torch / numpy / tree-sitter)
#   2) model    — Qwen3-Embedding-0.6B, downloaded on the first `index` run and
#                 cached under ~/.cache/huggingface
#   3) index    — the embedding index, built from origin/main
#
# Idempotent and non-interactive: a warm/cached container skips the install and
# only refreshes the index delta, so repeat runs are fast (seconds). The heavy
# pip + model download are paid ~once, on the first (cold) run.
#
# Usage: scripts/cloud-setup.sh [repo_dir]
set -euo pipefail

repo_dir="${1:-${CLAUDE_PROJECT_DIR:-$(pwd)}}"
cd "$repo_dir"

# Skip the (10–20s) editable-wheel rebuild when repoctx + the embedding stack are
# already importable — true on a warm/cached container, so this is a sub-second
# import check. The cold container still pays the full install once.
if python3 -c "import repoctx, sentence_transformers" 2>/dev/null; then
  echo "repoctx cloud-setup: packages already present — skipping install."
else
  echo "repoctx cloud-setup: installing packages (slow on a cold container, cached after)…"
  # CPU torch keeps the install lean — cloud sessions have no GPU. --extra-index-url
  # is additive: anything not on the CPU index still resolves from PyPI.
  python3 -m pip install -e ".[embeddings]" \
    --extra-index-url https://download.pytorch.org/whl/cpu
fi

echo "repoctx cloud-setup: refreshing embedding index…"
# `index --refresh` builds from scratch on the first run (downloading the Qwen3
# model, cached after) and on later runs only re-embeds the origin/main delta —
# a near-no-op (git fetch + diff, no model load) when the cached index is already
# current, which is what keeps warm sessions to a few seconds. CPU is explicit so
# we don't probe for absent CUDA/MPS. Non-fatal: a failure degrades retrieval to
# lexical (loudly) rather than blocking the session.
export REPOCTX_EMBEDDING_DEVICE="${REPOCTX_EMBEDDING_DEVICE:-cpu}"
if python3 -m repoctx index --refresh; then
  echo "repoctx cloud-setup: done — semantic retrieval ready."
else
  echo "repoctx cloud-setup: index refresh failed; retrieval will be lexical-only" \
       "until \`python3 -m repoctx index --refresh\` succeeds." >&2
fi
