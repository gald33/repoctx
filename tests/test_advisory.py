"""Opt-in advisory lane over in-flight branches ahead of origin/main (AC#5)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

numpy = pytest.importorskip("numpy")

from repoctx.advisory import (
    build_advisory_index,
    enumerate_advisory_branches,
    op_advisory_search,
)


class _SpyModel:
    def __init__(self, *a, **kw) -> None:
        self.device = "cpu"
        self.max_seq_length = 8192
        self.dtype = "fp32"

        class _Tok:
            model_max_length = 8192

        self.tokenizer = _Tok()

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
        return self

    def float(self):
        return self


def _patch_st():
    return patch.multiple(
        "repoctx.embeddings",
        HAS_EMBEDDINGS=True,
        SentenceTransformer=lambda *a, **kw: _SpyModel(),
    )


def _git(repo: Path, *args: str, env: dict | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True,
        env=env,
    ).stdout


def _base_repo(tmp: Path) -> Path:
    remote = tmp / "remote.git"
    remote.mkdir()
    _git(remote, "init", "--bare", "-q", "-b", "main")
    main = tmp / "main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    _git(main, "config", "user.email", "t@t.t")
    _git(main, "config", "user.name", "t")
    (main / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    _git(main, "add", "-A")
    _git(main, "commit", "-qm", "init")
    _git(main, "remote", "add", "origin", str(remote))
    _git(main, "push", "-q", "-u", "origin", "main")
    return main


def _branch_with_file(main: Path, branch: str, fname: str, *, old: bool = False) -> None:
    _git(main, "checkout", "-q", "-b", branch, "origin/main")
    (main / fname).write_text(f"def {branch.replace('-', '_')}():\n    return 1\n", encoding="utf-8")
    _git(main, "add", "-A")
    env = None
    if old:
        env = {**os.environ, "GIT_COMMITTER_DATE": "2000-01-01T00:00:00",
               "GIT_AUTHOR_DATE": "2000-01-01T00:00:00"}
    _git(main, "commit", "-qm", f"work on {branch}", env=env)
    _git(main, "checkout", "-q", "main")


def test_enumerate_includes_recent_ahead_branch(tmp_path: Path) -> None:
    main = _base_repo(tmp_path)
    _branch_with_file(main, "feat-x", "feature.py")

    infos = enumerate_advisory_branches(main)
    names = {b.name for b in infos}
    assert "feat-x" in names
    assert "main" not in names  # not ahead of origin/main
    feat = next(b for b in infos if b.name == "feat-x")
    assert feat.commits_ahead == 1


def test_enumerate_excludes_old_branch(tmp_path: Path) -> None:
    main = _base_repo(tmp_path)
    _branch_with_file(main, "stale-x", "stale.py", old=True)
    names = {b.name for b in enumerate_advisory_branches(main)}
    assert "stale-x" not in names


def test_enumerate_excludes_merged_branch(tmp_path: Path) -> None:
    main = _base_repo(tmp_path)
    _branch_with_file(main, "merged-x", "merged.py")
    # Merge into main and advance origin/main so merged-x is fully merged.
    _git(main, "merge", "-q", "--no-ff", "merged-x", "-m", "merge merged-x")
    _git(main, "push", "-q", "origin", "main")
    names = {b.name for b in enumerate_advisory_branches(main)}
    assert "merged-x" not in names


def test_build_and_search_returns_provenance(tmp_path: Path) -> None:
    main = _base_repo(tmp_path)
    _branch_with_file(main, "feat-x", "feature.py")

    with _patch_st():
        summary = build_advisory_index(main)
        assert summary["status"] == "built"
        assert summary["chunks"] >= 1
        result = op_advisory_search("feature", repo_root=main, top_k=5)

    assert result["status"] == "ok"
    assert result["lane"] == "advisory"
    assert result["results"], "advisory search should surface the branch's file"
    hit = next(h for h in result["results"] if h["path"] == "feature.py")
    assert hit["branch"] == "feat-x"
    assert hit["commits_ahead"] == 1
    assert hit["merge_status"] == "open"


def test_search_no_index_when_not_built(tmp_path: Path) -> None:
    main = _base_repo(tmp_path)
    with _patch_st():
        result = op_advisory_search("anything", repo_root=main)
    assert result["status"] == "no_index"
    assert "advisory-index" in result["message"]
    assert result["results"] == []


def test_build_with_no_qualifying_branches_is_ok_empty(tmp_path: Path) -> None:
    """Built but nothing in-flight → search reports ok/empty, not no_index."""
    main = _base_repo(tmp_path)  # only main, nothing ahead
    with _patch_st():
        summary = build_advisory_index(main)
        assert summary["status"] == "built"
        assert summary["chunks"] == 0
        result = op_advisory_search("anything", repo_root=main)
    assert result["status"] == "ok"
    assert result["results"] == []


def test_bundle_advisory_is_separate_from_authoritative(tmp_path: Path) -> None:
    main = _base_repo(tmp_path)
    _branch_with_file(main, "feat-x", "feature.py")
    from repoctx.protocol import op_bundle

    with _patch_st():
        build_advisory_index(main)
        payload = op_bundle("feature work", repo_root=main, include_advisory=True)

    # Advisory hits live under their own key, never in relevant_code/authority.
    assert "advisory" in payload
    adv_paths = {h["path"] for h in payload["advisory"]["results"]}
    assert "feature.py" in adv_paths
    relevant_paths = {r["path"] for r in payload["relevant_code"]}
    assert "feature.py" not in relevant_paths
    authority_paths = {r["path"] for r in payload["authority"]["records"]}
    assert "feature.py" not in authority_paths
