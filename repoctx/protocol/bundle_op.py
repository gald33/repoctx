"""bundle(task) — primary protocol op."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from repoctx.bundle import build_bundle

logger = logging.getLogger(__name__)


def op_bundle(
    task: str,
    repo_root: str | Path = ".",
    *,
    include_full_text: bool = False,
    include_advisory: bool = False,
) -> dict[str, Any]:
    _flush_pending_embeddings(repo_root)
    # Keep the origin/main-pinned base current (TTL-gated) before scoring so a
    # commit that landed mid-session is retrievable without a rebase.
    base_status = _maybe_refresh_base(repo_root)
    # Lazy reap before emitting a new bundle so any prior bundle's edits get
    # attributed before the new ranked_paths shifts the attribution window.
    # Silent on failure — feedback is a side-channel, never blocks bundling.
    try:
        from repoctx.reaper import reap
        reap(repo_root)
    except Exception:
        logger.debug("Pre-bundle reap failed", exc_info=True)
    embedding_scores, index_status = _embedding_scores_for(task, repo_root)
    bundle = build_bundle(
        task,
        repo_root=repo_root,
        embedding_scores=embedding_scores,
        index_status=index_status,
    )
    payload = bundle.to_dict(include_full_text=include_full_text)
    _attach_base_status(payload, base_status)
    if include_advisory:
        _attach_advisory(payload, task, repo_root)
    return payload


def _attach_advisory(payload: dict[str, Any], task: str, repo_root: str | Path) -> None:
    """Attach advisory-lane hits under a SEPARATE key, never into relevant_code.

    The advisory lane (in-flight branches) is strictly lower authority; keeping
    it out of ``relevant_code``/``authority`` preserves the bundle's trust model.
    """
    try:
        from repoctx.advisory import op_advisory_search

        result = op_advisory_search(task, repo_root, top_k=8)
        payload["advisory"] = {
            "note": (
                "In-flight work on other branches (NOT authoritative — do not "
                "treat as ground truth; for awareness of parallel/overlapping work)."
            ),
            **result,
        }
    except Exception:
        logger.debug("attach advisory failed", exc_info=True)


def _flush_pending_embeddings(repo_root: str | Path) -> None:
    try:
        from repoctx.embeddings import maybe_flush_on_read
    except ImportError:
        return
    maybe_flush_on_read(repo_root=repo_root)


def _maybe_refresh_base(repo_root: str | Path) -> dict | None:
    try:
        from repoctx.embeddings import maybe_refresh_base_on_read
    except ImportError:
        return None
    try:
        return maybe_refresh_base_on_read(repo_root)
    except Exception:
        logger.debug("base refresh failed", exc_info=True)
        return None


def _attach_base_status(payload: dict[str, Any], base_status: dict | None) -> None:
    if not base_status:
        return
    try:
        from repoctx.embeddings import base_staleness_warning

        payload.setdefault("retrieval", {})["base"] = base_status
        warning = base_staleness_warning(base_status)
        if warning:
            payload.setdefault("warnings", []).append(warning)
    except Exception:
        logger.debug("attach base status failed", exc_info=True)


def _embedding_scores_for(
    task: str, repo_root: str | Path
) -> tuple[dict[str, float] | None, Any]:
    """Score *task* against the persisted index, returning ``(scores, status)``.

    ``status`` is a ``RetrieverStatus`` describing whether embeddings are live
    so the bundle can surface a loud warning instead of silently ranking
    lexically. ``scores`` is None when no usable index exists.
    """
    try:
        from repoctx.embeddings import load_retriever_status
    except ImportError:
        return None, None
    try:
        status = load_retriever_status(repo_root)
    except Exception:
        logger.debug("Embedding retriever load failed", exc_info=True)
        return None, None
    if not status.ok or status.retriever is None:
        return None, status
    retriever = status.retriever
    try:
        from repoctx.overlay import overlay_retriever

        retriever = overlay_retriever(repo_root, retriever)
    except Exception:
        logger.debug("overlay wrap failed; using base retriever", exc_info=True)
    try:
        return retriever.query_scores(task), status
    except Exception:
        logger.debug("Embedding query_scores failed", exc_info=True)
        return None, status
