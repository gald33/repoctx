"""Read a repository tree directly from git objects — no checkout required.

The authoritative index is pinned to ``origin/main`` so it reflects *landed*
work even when the current worktree's branch predates it (the consuming repo
squash-merges and deletes branches, so mid-session ``origin/main`` advances and
the merged branch vanishes). Reading from git objects (``ls-tree`` +
``cat-file``) means the index is independent of whatever branch is checked out,
and costs no working-tree mutation.

A ``git fetch origin main`` is gated behind a TTL (or an explicit refresh) so
the network cost isn't paid on every call.
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path, PurePosixPath

from repoctx.config import DEFAULT_CONFIG, RepoCtxConfig
from repoctx.git_state import _run_git
from repoctx.index_location import index_state_root
from repoctx.models import RepositoryIndex
from repoctx.scanner import _add_record, build_file_record, is_supported_path

logger = logging.getLogger(__name__)

_CAT_FILE_TIMEOUT = 60  # batched read of a whole tree; generous but bounded

# Ref preference for the authoritative base. ``origin/main`` is the ground
# truth; the rest are fallbacks for repos without that remote/branch (local
# clones, CI checkouts, freshly-init'd repos).
BASE_REF_CANDIDATES = ("origin/main", "origin/HEAD", "main", "master", "HEAD")


def _fetch_stamp_path(repo_root: str | Path) -> Path:
    return index_state_root(repo_root) / "state" / "last_fetch_origin_main"


def maybe_fetch_origin_main(
    repo_root: str | Path, ttl_seconds: int, *, force: bool = False
) -> bool:
    """Fetch ``origin main`` if the TTL has lapsed (or ``force``).

    Returns ``True`` iff a fetch actually *succeeded*. Records the attempt time
    even on failure so an offline repo isn't hammered every call. Best-effort:
    never raises.
    """
    stamp = _fetch_stamp_path(repo_root)
    now = time.time()
    if not force:
        try:
            last = float(stamp.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            last = 0.0
        if now - last < ttl_seconds:
            return False
    out = _run_git(repo_root, "fetch", "--quiet", "origin", "main")
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(str(now), encoding="utf-8")
    except OSError:
        logger.debug("Could not write fetch stamp %s", stamp, exc_info=True)
    return out is not None


def resolve_base_ref(repo_root: str | Path) -> tuple[str, str] | None:
    """Resolve the authoritative base ref → ``(ref, full_sha)``.

    Tries ``origin/main`` first, then sensible fallbacks. Returns ``None`` when
    none resolve (e.g. an empty repo with no commits).
    """
    for ref in BASE_REF_CANDIDATES:
        out = _run_git(repo_root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        if out and out.strip():
            return ref, out.strip()
    return None


def iter_tree_blobs(
    repo_root: str | Path, ref: str, config: RepoCtxConfig = DEFAULT_CONFIG
) -> list[tuple[str, str]]:
    """List ``(path, blob_sha)`` for every supported file in ``ref``'s tree."""
    out = _run_git(repo_root, "ls-tree", "-r", "-z", ref)
    if not out:
        return []
    blobs: list[tuple[str, str]] = []
    for entry in out.split("\0"):
        if not entry:
            continue
        # "<mode> <type> <objectname>\t<path>"
        meta, _, path = entry.partition("\t")
        if not path:
            continue
        parts = meta.split()
        if len(parts) < 3 or parts[1] != "blob":
            continue
        sha = parts[2]
        if is_supported_path(path, config):
            blobs.append((path, sha))
    return blobs


def read_blobs(
    repo_root: str | Path, shas: list[str], max_bytes: int
) -> dict[str, str]:
    """Batch-read blob contents by sha via ``git cat-file --batch``.

    Returns ``{sha: text}`` (utf-8, errors ignored, truncated to ``max_bytes``).
    Missing objects are skipped. Deduplicates shas so identical-content files
    are read once.
    """
    unique = list(dict.fromkeys(shas))
    if not unique:
        return {}
    try:
        proc = subprocess.run(
            ["git", "cat-file", "--batch"],
            cwd=str(repo_root),
            input=("\n".join(unique) + "\n").encode("utf-8"),
            capture_output=True,
            timeout=_CAT_FILE_TIMEOUT,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        logger.debug("git cat-file --batch failed in %s: %s", repo_root, exc)
        return {}
    if proc.returncode != 0:
        return {}

    out = proc.stdout
    result: dict[str, str] = {}
    pos = 0
    n = len(out)
    while pos < n:
        nl = out.find(b"\n", pos)
        if nl == -1:
            break
        header = out[pos:nl].decode("utf-8", "replace")
        pos = nl + 1
        parts = header.split(" ")
        # "<oid> missing" → no content line follows.
        if len(parts) >= 2 and parts[1] == "missing":
            continue
        if len(parts) != 3:
            break  # unexpected; bail rather than misalign
        sha, _type, size_str = parts
        try:
            size = int(size_str)
        except ValueError:
            break
        content = out[pos : pos + size]
        pos += size + 1  # skip the trailing LF git appends after content
        result[sha] = content[:max_bytes].decode("utf-8", "ignore")
    return result


def scan_git_tree(
    repo_root: str | Path,
    ref: str,
    config: RepoCtxConfig = DEFAULT_CONFIG,
) -> RepositoryIndex:
    """Build a :class:`RepositoryIndex` from ``ref``'s tree (git objects only).

    Mirrors :func:`repoctx.scanner.scan_repository` but sources file content
    from git blobs instead of the working tree, so it is independent of the
    checked-out branch and never sees uncommitted edits.
    """
    root = Path(repo_root).resolve()
    blobs = iter_tree_blobs(root, ref, config)
    contents = read_blobs(root, [sha for _, sha in blobs], config.max_file_bytes)
    index = RepositoryIndex(root=root)
    for path, sha in blobs:
        content = contents.get(sha, "")
        record = build_file_record(path, content, root, config)
        _add_record(index, record)
    index.docs.sort(key=lambda item: (-item.doc_score, item.path))
    return index


__all__ = [
    "BASE_REF_CANDIDATES",
    "iter_tree_blobs",
    "maybe_fetch_origin_main",
    "read_blobs",
    "resolve_base_ref",
    "scan_git_tree",
]
