"""Worktree delta is overlaid on the origin/main base at query time (AC#4)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

numpy = pytest.importorskip("numpy")

from repoctx.config import DEFAULT_EMBEDDING_CONFIG
from repoctx.overlay import overlay_retriever, worktree_delta_paths


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


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout


def _remote_repo(tmp: Path) -> tuple[Path, Path]:
    remote = tmp / "remote.git"
    remote.mkdir()
    _git(remote, "init", "--bare", "-q", "-b", "main")
    main = tmp / "main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    _git(main, "config", "user.email", "t@t.t")
    _git(main, "config", "user.name", "t")
    (main / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    (main / "b.py").write_text("def b():\n    return 2\n", encoding="utf-8")
    _git(main, "add", "-A")
    _git(main, "commit", "-qm", "init")
    _git(main, "remote", "add", "origin", str(remote))
    _git(main, "push", "-q", "-u", "origin", "main")
    wt = tmp / "wt"
    _git(main, "worktree", "add", "-q", str(wt), "-b", "feat")
    return main, wt


def test_delta_classifies_ahead_dirty_and_deleted(tmp_path: Path) -> None:
    main, wt = _remote_repo(tmp_path)
    # Committed ahead on feat: new file c.py.
    (wt / "c.py").write_text("def c():\n    return 3\n", encoding="utf-8")
    _git(wt, "add", "c.py")
    _git(wt, "commit", "-qm", "add c")
    # Dirty: modify a.py, delete b.py, add an unsupported file.
    (wt / "a.py").write_text("def a():\n    return 99\n", encoding="utf-8")
    (wt / "b.py").unlink()
    (wt / "notes.bin").write_text("junk", encoding="utf-8")

    changed, deleted = worktree_delta_paths(wt)
    assert set(changed) == {"a.py", "c.py"}
    assert deleted == ["b.py"]
    assert "notes.bin" not in changed  # unsupported extension filtered


def test_overlay_adds_inprogress_and_removes_deleted(tmp_path: Path) -> None:
    main, wt = _remote_repo(tmp_path)
    with _patch_st():
        from repoctx.embeddings import EmbeddingModel, EmbeddingRetriever, build_index

        base = build_index(wt, source="origin-main")  # indexes a.py, b.py
        assert {e.path for e in base.entries} == {"a.py", "b.py"}
        retriever = EmbeddingRetriever(
            model=EmbeddingModel(DEFAULT_EMBEDDING_CONFIG), index=base,
        )

        # In-progress edits in the worktree: new untracked file, delete b.py.
        (wt / "c.py").write_text("def c():\n    return 3\n", encoding="utf-8")
        (wt / "b.py").unlink()

        effective = overlay_retriever(wt, retriever)

    paths = {e.path for e in effective.index.entries}
    assert paths == {"a.py", "c.py"}  # c.py overlaid in, b.py (deleted) removed


def test_overlay_noop_returns_base_when_clean(tmp_path: Path) -> None:
    main, wt = _remote_repo(tmp_path)
    with _patch_st():
        from repoctx.embeddings import EmbeddingModel, EmbeddingRetriever, build_index

        base = build_index(wt, source="origin-main")
        retriever = EmbeddingRetriever(
            model=EmbeddingModel(DEFAULT_EMBEDDING_CONFIG), index=base,
        )
        effective = overlay_retriever(wt, retriever)
    # No delta → exact same retriever object (no needless re-embed).
    assert effective is retriever


def test_overlay_disabled_via_env(tmp_path: Path, monkeypatch) -> None:
    main, wt = _remote_repo(tmp_path)
    monkeypatch.setenv("REPOCTX_OVERLAY_WORKTREE", "false")
    with _patch_st():
        from repoctx.embeddings import EmbeddingModel, EmbeddingRetriever, build_index

        base = build_index(wt, source="origin-main")
        retriever = EmbeddingRetriever(
            model=EmbeddingModel(DEFAULT_EMBEDDING_CONFIG), index=base,
        )
        (wt / "c.py").write_text("x = 1\n", encoding="utf-8")
        effective = overlay_retriever(wt, retriever)
    assert effective is retriever  # overlay off → base unchanged
