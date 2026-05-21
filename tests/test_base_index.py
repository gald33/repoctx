"""Authoritative index pinned to origin/main, refreshed via git objects (AC#3)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

numpy = pytest.importorskip("numpy")

from repoctx.embeddings import build_index, refresh_base_index
from repoctx.index_location import resolve_embeddings_dir, shared_embeddings_dir
from repoctx.vector_index import VectorIndex


# -- spy embedding model (no real download) ----------------------------------


class _SpyTokenizer:
    model_max_length = 8192


class _SpyModel:
    def __init__(self, *a, **kw) -> None:
        self.device = "cpu"
        self.max_seq_length = 8192
        self.dtype = "fp32"
        self.tokenizer = _SpyTokenizer()

    def get_sentence_embedding_dimension(self) -> int:
        return 8

    def encode(self, texts, **kwargs):
        if isinstance(texts, str):
            return numpy.zeros(8, dtype=numpy.float32)
        return numpy.zeros((len(texts), 8), dtype=numpy.float32)

    def to(self, device):
        self.device = device
        return self

    def half(self):
        self.dtype = "fp16"
        return self

    def float(self):
        self.dtype = "fp32"
        return self


def _patch_st():
    return patch.multiple(
        "repoctx.embeddings",
        HAS_EMBEDDINGS=True,
        SentenceTransformer=lambda *a, **kw: _SpyModel(),
    )


# -- git helpers --------------------------------------------------------------


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


def _remote_repo(tmp: Path) -> tuple[Path, Path, Path]:
    """Bare origin + main checkout (pushed) + a worktree on branch `feat`."""
    remote = tmp / "remote.git"
    remote.mkdir()
    _git(remote, "init", "--bare", "-q", "-b", "main")
    main = _repo(tmp / "main")
    (main / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    _commit_all(main, "init")
    _git(main, "remote", "add", "origin", str(remote))
    _git(main, "push", "-q", "-u", "origin", "main")
    wt = tmp / "wt"
    _git(main, "worktree", "add", "-q", str(wt), "-b", "feat")
    return remote, main, wt


def _index_paths(d: Path) -> set[str]:
    return {e.path for e in VectorIndex.load(d).entries}


# -- tests --------------------------------------------------------------------


def test_build_origin_main_indexes_committed_tree(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "r")
    (repo / "a.py").write_text("committed = 1\n", encoding="utf-8")
    _commit_all(repo, "init")
    (repo / "a.py").write_text("DIRTY = 2\n", encoding="utf-8")  # dirty working tree
    (repo / "untracked.py").write_text("z = 3\n", encoding="utf-8")

    with _patch_st():
        idx = build_index(repo, source="origin-main")

    paths = {e.path for e in idx.entries}
    assert "a.py" in paths
    assert "untracked.py" not in paths  # committed tree only
    assert idx.source_meta["built_from"] == "origin-main"
    assert idx.source_meta["base_ref"] in ("main", "HEAD")
    assert idx.source_meta["base_sha"]


def test_landed_commit_retrievable_from_stale_worktree(tmp_path: Path) -> None:
    """AC#3: a commit merged into origin/main mid-session is retrievable from a
    worktree whose branch predates it, after a refresh — no rebase/checkout."""
    remote, main, wt = _remote_repo(tmp_path)
    shared = shared_embeddings_dir(wt)

    # Build the authoritative index from the worktree (branch `feat`).
    with _patch_st():
        build_index(wt, source="origin-main").save(shared)
    assert _index_paths(shared) == {"a.py"}

    # Meanwhile, work lands on origin/main (and the branch is irrelevant to wt).
    (main / "b.py").write_text("def b():\n    return 2\n", encoding="utf-8")
    _commit_all(main, "add b")
    _git(main, "push", "-q", "origin", "main")

    # The worktree is still on `feat`, which does NOT contain b.py.
    assert "b.py" not in (wt / "b.py").parts or not (wt / "b.py").exists()

    # Refresh from the worktree: fetch origin/main + re-embed the delta.
    with _patch_st():
        result = refresh_base_index(wt, force=True)

    assert result["status"] == "refreshed"
    assert result["base_ref"] == "origin/main"
    # b.py — landed on main, absent from the worktree's branch — is now indexed.
    assert _index_paths(shared) == {"a.py", "b.py"}


def test_refresh_is_current_when_base_unchanged(tmp_path: Path) -> None:
    remote, main, wt = _remote_repo(tmp_path)
    with _patch_st():
        build_index(wt, source="origin-main").save(shared_embeddings_dir(wt))
        result = refresh_base_index(wt)  # nothing landed since
    assert result["status"] == "current"


def test_op_bundle_reports_healthy_retrieval_over_shared_index(tmp_path: Path) -> None:
    """End-to-end: a base index built from one worktree makes bundle report
    embeddings-active (no degradation warning) when called from that worktree."""
    remote, main, wt = _remote_repo(tmp_path)
    with _patch_st():
        build_index(wt, source="origin-main").save(shared_embeddings_dir(wt))
        from repoctx.protocol import op_bundle

        payload = op_bundle("work on a", repo_root=wt)

    assert payload["retrieval"]["ranker"] == "embeddings"
    assert payload["retrieval"]["index_status"] == "ok"
    assert payload["retrieval"]["embeddings_active"] is True
    # No "no embedding index" degradation warning on the healthy path.
    assert not any("No embedding index" in w for w in payload["warnings"])
    # Base is pinned to origin/main and current (nothing landed since build).
    assert payload["retrieval"]["base"]["status"] == "current"


def test_read_path_probe_reports_stale_without_reembedding(tmp_path: Path) -> None:
    """embed=False (warn-only mode) reports drift but does not rebuild."""
    remote, main, wt = _remote_repo(tmp_path)
    shared = shared_embeddings_dir(wt)
    with _patch_st():
        build_index(wt, source="origin-main").save(shared)

    (main / "b.py").write_text("y = 1\n", encoding="utf-8")
    _commit_all(main, "add b")
    _git(main, "push", "-q", "origin", "main")

    # Warn-only: no model needed, index untouched.
    result = refresh_base_index(wt, embed=False)
    assert result["status"] == "stale"
    assert result["changed"] == 1
    assert _index_paths(shared) == {"a.py"}  # unchanged

    from repoctx.embeddings import base_staleness_warning

    assert "repoctx index --refresh" in base_staleness_warning(result)
