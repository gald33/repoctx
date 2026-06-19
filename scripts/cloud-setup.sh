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
# Idempotent and non-interactive. The container filesystem is cached after the
# first successful run, so the heavy pip + model download are paid ~once.
#
# Usage: scripts/cloud-setup.sh [repo_dir]
set -euo pipefail

repo_dir="${1:-${CLAUDE_PROJECT_DIR:-$(pwd)}}"
cd "$repo_dir"

echo "repoctx cloud-setup: installing packages (slow on a cold container, cached after)…"
# CPU torch keeps the install lean — cloud sessions have no GPU. --extra-index-url
# is additive: anything not on the CPU index still resolves from PyPI.
python3 -m pip install -e ".[embeddings]" \
  --extra-index-url https://download.pytorch.org/whl/cpu

echo "repoctx cloud-setup: building embedding index (downloads the model on first run)…"
# CPU is explicit so we don't probe for CUDA/MPS that isn't here. A failed build
# is non-fatal: retrieval falls back to lexical (loudly) rather than blocking the
# session — re-run `python3 -m repoctx index` to fix.
export REPOCTX_EMBEDDING_DEVICE="${REPOCTX_EMBEDDING_DEVICE:-cpu}"
if python3 -m repoctx index; then
  echo "repoctx cloud-setup: done — semantic retrieval ready."
else
  echo "repoctx cloud-setup: index build failed; retrieval will be lexical-only" \
       "until \`python3 -m repoctx index\` succeeds." >&2
fi
