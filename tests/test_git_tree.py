"""Reading a repo tree from git objects (origin/main pinning, no checkout)."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from repoctx.git_tree import (
    iter_tree_blobs,
    maybe_fetch_origin_main,
    read_blobs,
    resolve_base_ref,
    scan_git_tree,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout


def _repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@t.t")
    _git(path, "config", "user.name", "t")
    return path


def _commit_all(repo: Path, msg: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", msg)
    return _git(repo, "rev-parse", "HEAD").strip()


def test_iter_tree_blobs_lists_supported_files(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "README.md").write_text("# hi\n", encoding="utf-8")
    (repo / "image.png").write_text("not really\n", encoding="utf-8")  # unsupported ext
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "dep.py").write_text("y = 2\n", encoding="utf-8")  # ignored dir
    _commit_all(repo, "init")

    paths = {p for p, _sha in iter_tree_blobs(repo, "HEAD")}
    assert "a.py" in paths
    assert "README.md" in paths
    assert "image.png" not in paths
    assert "node_modules/dep.py" not in paths


def test_scan_git_tree_reads_committed_not_working_tree(tmp_path: Path) -> None:
    """The index must reflect committed bytes, ignoring dirty working-tree edits."""
    repo = _repo(tmp_path / "r")
    (repo / "a.py").write_text("committed = True\n", encoding="utf-8")
    _commit_all(repo, "init")
    # Dirty the working tree *after* committing.
    (repo / "a.py").write_text("DIRTY = 999\n", encoding="utf-8")
    (repo / "untracked.py").write_text("nope = 1\n", encoding="utf-8")

    index = scan_git_tree(repo, "HEAD")
    assert "a.py" in index.records
    assert index.records["a.py"].content == "committed = True\n"
    # Uncommitted file is not part of the committed tree.
    assert "untracked.py" not in index.records


def test_read_blobs_returns_content_by_sha(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    (repo / "a.py").write_text("hello = 1\n", encoding="utf-8")
    _commit_all(repo, "init")
    blobs = iter_tree_blobs(repo, "HEAD")
    shas = [sha for _p, sha in blobs]
    contents = read_blobs(repo, shas, max_bytes=10_000)
    assert any("hello = 1" in v for v in contents.values())


def test_resolve_base_ref_prefers_origin_main(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "--bare", "-q", "-b", "main")
    main = _repo(tmp_path / "main")
    (main / "a.py").write_text("x = 1\n", encoding="utf-8")
    _commit_all(main, "init")
    _git(main, "remote", "add", "origin", str(remote))
    _git(main, "push", "-q", "-u", "origin", "main")

    ref, sha = resolve_base_ref(main)
    assert ref == "origin/main"
    assert sha == _git(main, "rev-parse", "origin/main").strip()


def test_resolve_base_ref_falls_back_to_head(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    head = _commit_all(repo, "init")
    ref, sha = resolve_base_ref(repo)
    # No remote: falls back to a local ref (main or HEAD), pointing at the commit.
    assert ref in ("main", "HEAD")
    assert sha == head


def test_maybe_fetch_ttl_gates_calls(tmp_path: Path, monkeypatch) -> None:
    """Within the TTL, no fetch is attempted; force always fetches."""
    repo = _repo(tmp_path / "r")
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    _commit_all(repo, "init")

    calls: list[tuple] = []

    def fake_run_git(root, *args):
        if args[:1] == ("fetch",):
            calls.append(args)
            return ""  # success, no output
        return None

    monkeypatch.setattr("repoctx.git_tree._run_git", fake_run_git)

    # First call (no stamp yet) → fetches.
    assert maybe_fetch_origin_main(repo, ttl_seconds=1000) is True
    assert len(calls) == 1
    # Second call within TTL → skipped.
    assert maybe_fetch_origin_main(repo, ttl_seconds=1000) is False
    assert len(calls) == 1
    # Forced → fetches regardless of TTL.
    assert maybe_fetch_origin_main(repo, ttl_seconds=1000, force=True) is True
    assert len(calls) == 2
