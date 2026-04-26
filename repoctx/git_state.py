"""Lightweight git probing — HEAD sha, dirty file detection.

Used to attach freshness/staleness markers to bundles so agents can tell
whether a bundle reflects committed state or in-flight edits.

Pure stdlib (``subprocess``); silent fallback on non-git repos.
"""

from __future__ import annotations

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


__all__ = ["collect_state", "dirty_files", "head_branch", "head_sha"]
