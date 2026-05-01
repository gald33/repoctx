"""bundle(task) — primary protocol op."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from repoctx.bundle import build_bundle

logger = logging.getLogger(__name__)


def op_bundle(task: str, repo_root: str | Path = ".", *, include_full_text: bool = False) -> dict[str, Any]:
    _flush_pending_embeddings(repo_root)
    embedding_scores = _embedding_scores_for(task, repo_root)
    bundle = build_bundle(task, repo_root=repo_root, embedding_scores=embedding_scores)
    return bundle.to_dict(include_full_text=include_full_text)


def _flush_pending_embeddings(repo_root: str | Path) -> None:
    try:
        from repoctx.embeddings import maybe_flush_on_read
    except ImportError:
        return
    maybe_flush_on_read(repo_root=repo_root)


def _embedding_scores_for(task: str, repo_root: str | Path) -> dict[str, float] | None:
    """Load the persisted embedding index (if any) and score *task* against it.

    Returns None when the [embeddings] extra is missing, no index has been
    built, or the index is schema-incompatible — leaving the bundle assembler
    to fall back to pure-lexical ranking.
    """
    try:
        from repoctx.embeddings import try_load_retriever
    except ImportError:
        return None
    try:
        retriever = try_load_retriever(repo_root)
    except Exception:
        logger.debug("Embedding retriever load failed", exc_info=True)
        return None
    if retriever is None:
        return None
    try:
        return retriever.query_scores(task)
    except Exception:
        logger.debug("Embedding query_scores failed", exc_info=True)
        return None
