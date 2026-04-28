"""semantic_search(query) — direct top-K chunk lookup over the embedding index.

Exposes the per-chunk vector index as a primitive so an agent can run its
own similarity queries instead of receiving a task-shaped bundle. The
existing retrieval path (`bundle`, `get_task_context`, `scope`) blends
embeddings with heuristic scoring and aggregates to a per-file score; this
op skips both, returning the raw top-K chunks ordered by cosine similarity.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from repoctx.config import DEFAULT_EMBEDDING_CONFIG, EmbeddingConfig
from repoctx.embeddings import try_load_retriever

logger = logging.getLogger(__name__)

DEFAULT_SNIPPET_CHARS = 500
ALLOWED_KINDS = ("code", "doc", "test", "config")


def op_semantic_search(
    query: str,
    repo_root: str | Path = ".",
    *,
    top_k: int = 10,
    kind: str | None = None,
    config: EmbeddingConfig = DEFAULT_EMBEDDING_CONFIG,
    snippet_chars: int = DEFAULT_SNIPPET_CHARS,
) -> list[dict[str, Any]]:
    """Return the top-K most similar indexed chunks for *query*.

    Each result is a dict with keys ``path``, ``score``, ``snippet``,
    ``start_line``, ``end_line``, ``enclosing_symbol``. Sorted by
    descending cosine similarity.

    Returns ``[]`` (with a clear log message) if no embedding index has
    been built — agents may not have run ``repoctx index`` yet, and the
    op should never raise on the cold-start path.

    ``kind`` filters to one of ``"code" | "doc" | "test" | "config"``;
    other values are ignored with a warning.
    """
    if top_k <= 0:
        return []

    root = Path(repo_root).resolve()
    retriever = try_load_retriever(root, config=config)
    if retriever is None:
        logger.info(
            "semantic_search: no embedding index at %s; "
            "run `repoctx index` to build one.",
            root,
        )
        return []

    if kind is not None and kind not in ALLOWED_KINDS:
        logger.warning(
            "semantic_search: ignoring unknown kind=%r (allowed: %s)",
            kind, ", ".join(ALLOWED_KINDS),
        )
        kind = None

    query_vec = retriever.model.encode_query(query)
    scored = retriever.index.similarity_scores_by_id(query_vec)

    hits: list[dict[str, Any]] = []
    file_cache: dict[str, list[str]] = {}
    for path, score, entry in scored:
        if kind is not None and entry.kind != kind:
            continue
        meta = entry.metadata or {}
        start_line = int(meta.get("start_line", 1))
        end_line = int(meta.get("end_line", start_line))
        snippet = _load_snippet(
            root, path, start_line, end_line, snippet_chars, file_cache,
        )
        hits.append(
            {
                "path": path,
                "score": float(score),
                "snippet": snippet,
                "start_line": start_line,
                "end_line": end_line,
                "enclosing_symbol": meta.get("enclosing_symbol"),
            }
        )
        if len(hits) >= top_k:
            break
    return hits


def _load_snippet(
    repo_root: Path,
    rel_path: str,
    start_line: int,
    end_line: int,
    max_chars: int,
    cache: dict[str, list[str]],
) -> str:
    """Slice ``[start_line-1:end_line]`` from *rel_path* and truncate to *max_chars*.

    Lines are read once per file via the *cache* dict so multi-chunk
    files don't re-hit disk. Missing/unreadable files return an empty
    string rather than raising — the index can outlive a file rename.
    """
    lines = cache.get(rel_path)
    if lines is None:
        try:
            text = (repo_root / rel_path).read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            cache[rel_path] = []
            return ""
        lines = text.splitlines(keepends=True)
        cache[rel_path] = lines
    if not lines:
        return ""
    lo = max(0, start_line - 1)
    hi = max(lo, end_line)
    snippet = "".join(lines[lo:hi])
    if len(snippet) > max_chars:
        snippet = snippet[:max_chars]
    return snippet


__all__ = ["op_semantic_search"]
