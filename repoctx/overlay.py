"""Overlay the current worktree's delta on top of the origin/main base index.

The authoritative index is pinned to ``origin/main`` (landed work). But an
agent working in a worktree also needs *its own* in-progress work to be
retrievable — otherwise it can't find the function it just wrote. This module
layers the worktree delta on top of the base at query time, so the effective
index models "fresh origin/main ∪ my in-progress work" (what the tree will look
like after a rebase).

Delta = files changed in commits ahead of the base (``merge-base..HEAD``) plus
uncommitted/untracked edits. For each, the *current working-tree* bytes win
over (or remove) the base's copy. Only the delta is embedded per query — and it
is cached by content hash within the process — so the cost is bounded by how
much you've changed, not repo size.

Sibling worktrees' uncommitted bytes are deliberately NOT indexed here (see the
advisory lane for committed branches); this overlay is strictly *this*
worktree's own changes.
"""

from __future__ import annotations

import logging
from pathlib import Path

from repoctx.config import DEFAULT_EMBEDDING_CONFIG, EmbeddingConfig
from repoctx.git_state import dirty_files
from repoctx.scanner import is_supported_path

logger = logging.getLogger(__name__)

# Process-local cache: repo_root → (delta_signature, overlay_index). Keyed so a
# repeated query within a session reuses the embedded delta instead of
# re-embedding it. Invalidated automatically when any delta file's content hash
# changes (the signature shifts).
_OVERLAY_CACHE: dict[str, tuple[tuple, object]] = {}


def worktree_delta_paths(
    repo_root: Path, config=None
) -> tuple[list[str], list[str]]:
    """Return ``(changed_paths, deleted_paths)`` of this worktree vs the base.

    ``changed`` = supported files that exist in the worktree and differ from the
    base (commits ahead ∪ dirty). ``deleted`` = supported files in the delta
    that no longer exist in the worktree (so they must be removed from results).
    """
    from repoctx.config import DEFAULT_CONFIG

    scan_cfg = config or DEFAULT_CONFIG
    from repoctx.git_tree import resolve_base_ref
    from repoctx.git_state import _run_git

    candidates: set[str] = set()
    resolved = resolve_base_ref(repo_root)
    if resolved is not None:
        ref, _sha = resolved
        # Three-dot: changes on HEAD since it diverged from the base.
        out = _run_git(repo_root, "diff", "--name-only", f"{ref}...HEAD")
        if out:
            candidates.update(ln for ln in out.splitlines() if ln.strip())
    candidates.update(dirty_files(repo_root))

    changed: list[str] = []
    deleted: list[str] = []
    for rel in sorted(candidates):
        if not is_supported_path(rel, scan_cfg):
            continue
        if (repo_root / rel).exists():
            changed.append(rel)
        else:
            deleted.append(rel)
    return changed, deleted


def _delta_signature(repo_root: Path, changed: list[str], deleted: list[str]) -> tuple:
    from repoctx.embeddings import content_hash
    from repoctx.scanner import _read_text

    parts: list[tuple[str, str]] = []
    for rel in changed:
        try:
            text = _read_text(repo_root / rel, 1_000_000)
        except OSError:
            text = ""
        parts.append((rel, content_hash(text)))
    return (tuple(parts), tuple(sorted(deleted)))


def _build_overlay_index(repo_root: Path, model, changed: list[str], config):
    """Embed the changed working-tree files into a small VectorIndex."""
    from repoctx.chunker import ChunkConfig
    from repoctx.embeddings import (
        _chunk_to_entry,
        _chunks_for_record,
        build_enriched_chunk_text,
    )
    from repoctx.config import DEFAULT_CONFIG
    from repoctx.scanner import _read_text, build_file_record
    from repoctx.vector_index import VectorIndex

    chunk_cfg = ChunkConfig()
    texts: list[str] = []
    entries = []
    for rel in changed:
        content = _read_text(repo_root / rel, DEFAULT_CONFIG.max_file_bytes)
        record = build_file_record(rel, content, repo_root, DEFAULT_CONFIG)
        chunks = _chunks_for_record(record, chunk_cfg)
        for c in chunks:
            texts.append(build_enriched_chunk_text(record, c))
            entries.append(_chunk_to_entry(record, c))
    if not entries:
        return VectorIndex(vectors=None, entries=[], model_name=model_name_of(model), dimension=0)
    vectors = model.encode_documents(texts, show_progress=False)
    return VectorIndex(
        vectors=vectors,
        entries=entries,
        model_name=model_name_of(model),
        dimension=int(vectors.shape[1]),
    )


def model_name_of(model) -> str:
    cfg = getattr(model, "config", None)
    if cfg is not None and getattr(cfg, "model_name", ""):
        return cfg.model_name
    return getattr(model, "model_name", "") or ""


def _merge_indexes(base, overlay, drop_paths: set[str]):
    """Base minus ``drop_paths``, plus all overlay entries → a new VectorIndex."""
    import numpy as np

    from repoctx.vector_index import VectorIndex

    keep_entries = []
    keep_rows = []
    for i, e in enumerate(base.entries):
        if e.path in drop_paths:
            continue
        keep_entries.append(e)
        keep_rows.append(i)

    base_vectors = base.vectors[keep_rows] if (base.vectors is not None and keep_rows) else None
    parts = [v for v in (base_vectors, getattr(overlay, "vectors", None)) if v is not None and len(v)]
    if parts:
        vectors = np.vstack(parts)
    else:
        vectors = None
    dim = base.dimension or getattr(overlay, "dimension", 0)
    return VectorIndex(
        vectors=vectors,
        entries=[*keep_entries, *overlay.entries],
        model_name=base.model_name,
        dimension=dim,
        chunk_config=base.chunk_config,
        source_meta=base.source_meta,
    )


def overlay_retriever(
    repo_root: str | Path,
    base_retriever,
    config: EmbeddingConfig = DEFAULT_EMBEDDING_CONFIG,
):
    """Wrap ``base_retriever`` so queries see origin/main ∪ this worktree's delta.

    Returns the base retriever unchanged when there's no delta, when the
    overlay is disabled, or when the delta is over the safety cap. Best-effort:
    any failure falls back to the base retriever.
    """
    import os

    root = Path(repo_root).resolve()
    enabled = config.overlay_worktree
    raw = os.environ.get("REPOCTX_OVERLAY_WORKTREE")
    if raw is not None:
        enabled = raw.strip().lower() in ("1", "true", "yes", "on")
    if not enabled:
        return base_retriever
    try:
        changed, deleted = worktree_delta_paths(root)
    except Exception:  # noqa: BLE001
        logger.debug("overlay: delta computation failed", exc_info=True)
        return base_retriever
    if not changed and not deleted:
        return base_retriever
    if len(changed) + len(deleted) > config.overlay_max_files:
        logger.info(
            "overlay: %d delta files exceed cap %d; skipping overlay",
            len(changed) + len(deleted), config.overlay_max_files,
        )
        return base_retriever

    try:
        from repoctx.embeddings import EmbeddingRetriever

        signature = _delta_signature(root, changed, deleted)
        cached = _OVERLAY_CACHE.get(str(root))
        if cached is not None and cached[0] == signature:
            overlay_index = cached[1]
        else:
            overlay_index = _build_overlay_index(root, base_retriever.model, changed, config)
            _OVERLAY_CACHE[str(root)] = (signature, overlay_index)

        drop = set(changed) | set(deleted)
        effective = _merge_indexes(base_retriever.index, overlay_index, drop)
        return EmbeddingRetriever(model=base_retriever.model, index=effective)
    except Exception:  # noqa: BLE001 — overlay must never break retrieval
        logger.debug("overlay: build failed; using base", exc_info=True)
        return base_retriever


__all__ = ["overlay_retriever", "worktree_delta_paths"]
