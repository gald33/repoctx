"""Lightweight git probing — HEAD sha, dirty file detection.

Used to attach freshness/staleness markers to bundles so agents can tell
whether a bundle reflects committed state or in-flight edits.

Pure stdlib (``subprocess``); silent fallback on non-git repos.
"""

from __future__ import annotations

import functools
import logging
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_GIT_TIMEOUT = 5  # seconds; git on a healthy repo is sub-second


def _run_git(repo_root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("git invocation failed in %s: %s", repo_root, exc)
        return None
    if result.returncode != 0:
        return None
    return result.stdout


@functools.lru_cache(maxsize=256)
def _git_common_dir_cached(repo_root_str: str) -> str | None:
    """Cached worker for :func:`git_common_dir` (keyed by resolved path str).

    The common dir of a checkout is stable for a process's lifetime, and the
    read/queue paths call this on every tool invocation, so we memoize the
    subprocess away.
    """
    out = _run_git(Path(repo_root_str), "rev-parse", "--git-common-dir")
    if not out:
        return None
    raw = out.strip()
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = Path(repo_root_str) / p
    try:
        return str(p.resolve())
    except OSError:
        return str(p)


def git_common_dir(repo_root: Path) -> Path | None:
    """Return the *shared* git dir for ``repo_root``, or ``None`` if not a repo.

    All linked worktrees of a repository — and the main checkout — resolve to
    the **same** common dir (``git rev-parse --git-common-dir``), so it is the
    natural identity to key a per-repo index on: an index built from any
    worktree is found from every other. Returns an absolute, resolved path.
    """
    cached = _git_common_dir_cached(str(Path(repo_root).resolve()))
    return Path(cached) if cached is not None else None


def head_sha(repo_root: Path) -> str | None:
    """Return short HEAD sha, or ``None`` if not a git repo."""
    out = _run_git(repo_root, "rev-parse", "--short", "HEAD")
    return out.strip() if out else None


def head_branch(repo_root: Path) -> str | None:
    """Return symbolic branch name; ``None`` on detached HEAD or non-git."""
    out = _run_git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    if not out:
        return None
    name = out.strip()
    return None if name in {"", "HEAD"} else name


def dirty_files(repo_root: Path) -> list[str]:
    """Return repo-relative paths with uncommitted changes (incl. untracked).

    Empty list both for clean repos and non-git directories.
    """
    out = _run_git(repo_root, "status", "--porcelain", "-z", "--untracked-files=all")
    if out is None:
        return []
    paths: list[str] = []
    for entry in out.split("\0"):
        if not entry or len(entry) < 4:
            continue
        # Porcelain v1 format: 'XY path' (status code + space + path).
        path = entry[3:]
        # Renames look like 'old -> new'; in -z mode the new path comes in the
        # next field, so the bare path is sufficient.
        if path:
            paths.append(path)
    return paths


def collect_state(repo_root: Path, scope_paths: list[str] | None = None) -> dict[str, Any]:
    """Bundle-friendly state snapshot.

    Returns ``{}`` when not a git repo, so callers can ``if state:`` and skip.
    """
    sha = head_sha(repo_root)
    if sha is None:
        return {}
    dirty = dirty_files(repo_root)
    state: dict[str, Any] = {
        "head_sha": sha,
        "branch": head_branch(repo_root),
        "dirty_file_count": len(dirty),
        "dirty_files": dirty[:20],  # cap; full list can be huge
    }
    if scope_paths is not None:
        scope_set = set(scope_paths)
        in_scope = sorted(p for p in dirty if p in scope_set)
        state["dirty_in_scope"] = in_scope
    return state


__all__ = ["collect_state", "dirty_files", "git_common_dir", "head_branch", "head_sha"]
