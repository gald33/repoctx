"""Where the embedding index lives — keyed by repo identity, not by cwd.

Historically repoctx stored the index inside the working tree at
``<cwd>/.repoctx/embeddings``. A git *worktree* is a separate working
directory, so an index built in the main checkout was invisible to every
worktree — retrieval silently fell back to lexical (see
``docs/plans/2026-05-21-worktree-index-design`` rationale in the README).

The fix: key the index on the repository's **shared git common dir**
(``git rev-parse --git-common-dir``), which every linked worktree and the
main checkout resolve to identically. The index lives at
``<git-common-dir>/repoctx/embeddings`` — outside every working tree (so it
never shows up as dirty/untracked and is never committed) and shared by all
worktrees automatically.

Non-git directories (and the fake-``.git`` dirs used in unit tests) fall back
to the legacy in-tree location so existing behavior is preserved.

All read/write call sites must go through :func:`resolve_embeddings_dir` so
the location stays a single pure function of the repo.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from repoctx.config import DEFAULT_EMBEDDING_CONFIG, EmbeddingConfig
from repoctx.git_state import git_common_dir

logger = logging.getLogger(__name__)

# Subdir under the git common dir that holds all per-identity repoctx state
# (embeddings, advisory index, fetch timestamps, …).
INDEX_NAMESPACE = "repoctx"
EMBEDDINGS_SUBDIR = "embeddings"


def index_state_root(
    repo_root: str | Path, config: EmbeddingConfig = DEFAULT_EMBEDDING_CONFIG
) -> Path:
    """Root for all per-repo derived state, shared across worktrees.

    ``<git-common-dir>/repoctx`` when in a git repo, else the legacy in-tree
    ``<repo_root>/.repoctx``.
    """
    root = Path(repo_root).resolve()
    common = git_common_dir(root)
    if common is not None:
        return common / INDEX_NAMESPACE
    return root / config.index_dir


def shared_embeddings_dir(
    repo_root: str | Path, config: EmbeddingConfig = DEFAULT_EMBEDDING_CONFIG
) -> Path:
    """The canonical (identity-keyed) embeddings dir. Always the write target."""
    return index_state_root(repo_root, config) / EMBEDDINGS_SUBDIR


def legacy_embeddings_dir(
    repo_root: str | Path, config: EmbeddingConfig = DEFAULT_EMBEDDING_CONFIG
) -> Path:
    """The pre-1.5 in-tree embeddings dir (``<repo_root>/.repoctx/embeddings``)."""
    return Path(repo_root).resolve() / config.index_dir / EMBEDDINGS_SUBDIR


def _has_index(d: Path) -> bool:
    # A real index always has vectors.npy; the marker file is enough to tell
    # "something was built here" from "empty/absent dir".
    return (d / "vectors.npy").exists()


def migrate_legacy_index_if_needed(
    repo_root: str | Path, config: EmbeddingConfig = DEFAULT_EMBEDDING_CONFIG
) -> bool:
    """Move a legacy in-tree index to the shared location, once.

    Returns ``True`` if a migration happened. No-op (returns ``False``) when
    the shared index already exists, when there's no legacy index, or when the
    two resolve to the same path (non-git fallback). Best-effort: on any error
    the legacy index is left untouched so the read path can still use it.
    """
    shared = shared_embeddings_dir(repo_root, config)
    legacy = legacy_embeddings_dir(repo_root, config)
    if shared == legacy:
        return False
    if _has_index(shared):
        return False
    if not _has_index(legacy):
        return False
    try:
        shared.parent.mkdir(parents=True, exist_ok=True)
        if shared.exists():
            shutil.rmtree(shared)
        shutil.move(str(legacy), str(shared))
        logger.warning(
            "Migrated repoctx embedding index from in-tree %s to shared %s "
            "(now visible from every worktree).",
            legacy, shared,
        )
        return True
    except OSError:
        logger.warning(
            "Could not migrate legacy index %s → %s; reading it in place. "
            "Run `repoctx rebuild` to relocate.",
            legacy, shared, exc_info=True,
        )
        return False


def resolve_embeddings_dir(
    repo_root: str | Path,
    config: EmbeddingConfig = DEFAULT_EMBEDDING_CONFIG,
    *,
    migrate: bool = True,
) -> Path:
    """The embeddings dir to read/write for ``repo_root``.

    Prefers the shared, identity-keyed location. When ``migrate`` is set
    (default) and only a legacy in-tree index exists, relocates it first. If
    relocation fails, transparently returns the legacy path so retrieval keeps
    working. When neither exists, returns the shared path (the build target).
    """
    shared = shared_embeddings_dir(repo_root, config)
    if _has_index(shared):
        return shared
    legacy = legacy_embeddings_dir(repo_root, config)
    if shared != legacy and _has_index(legacy):
        if migrate and migrate_legacy_index_if_needed(repo_root, config) and _has_index(shared):
            return shared
        if _has_index(shared):
            return shared
        return legacy
    return shared


__all__ = [
    "index_state_root",
    "legacy_embeddings_dir",
    "migrate_legacy_index_if_needed",
    "resolve_embeddings_dir",
    "shared_embeddings_dir",
]
