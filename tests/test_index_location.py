"""Index storage is keyed by repo identity and shared across worktrees."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

numpy = pytest.importorskip("numpy")

from repoctx.git_state import git_common_dir
from repoctx.index_location import (
    legacy_embeddings_dir,
    migrate_legacy_index_if_needed,
    resolve_embeddings_dir,
    shared_embeddings_dir,
)
from repoctx.vector_index import IndexEntry, VectorIndex


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@t.t")
    _git(path, "config", "user.name", "t")
    (path / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    _git(path, "add", "a.py")
    _git(path, "commit", "-qm", "init")
    return path


def _add_worktree(main: Path, wt: Path, branch: str) -> Path:
    _git(main, "worktree", "add", "-q", str(wt), "-b", branch)
    return wt


def _tiny_index() -> VectorIndex:
    vectors = numpy.eye(2, dtype=numpy.float32)
    entries = [
        IndexEntry(path="a.py", kind="code", content_hash="h0", record_type="chunk",
                   metadata={"chunk_index": 0, "start_line": 1, "end_line": 2}),
        IndexEntry(path="b.py", kind="code", content_hash="h1", record_type="chunk",
                   metadata={"chunk_index": 0, "start_line": 1, "end_line": 2}),
    ]
    return VectorIndex(vectors=vectors, entries=entries, model_name="fake", dimension=2)


# -- identity ----------------------------------------------------------------


def test_common_dir_identical_for_main_and_worktree(tmp_path: Path) -> None:
    main = _init_repo(tmp_path / "main")
    wt = _add_worktree(main, tmp_path / "wt-a", "feat")
    assert git_common_dir(main) == git_common_dir(wt)
    # And it points at the *main* checkout's .git.
    assert git_common_dir(wt) == (main / ".git").resolve()


def test_shared_embeddings_dir_identical_across_worktrees(tmp_path: Path) -> None:
    main = _init_repo(tmp_path / "main")
    wt = _add_worktree(main, tmp_path / "wt-a", "feat")
    assert shared_embeddings_dir(main) == shared_embeddings_dir(wt)
    assert shared_embeddings_dir(wt).is_relative_to((main / ".git").resolve())


def test_non_git_dir_falls_back_to_legacy_in_tree(tmp_path: Path) -> None:
    d = tmp_path / "plain"
    d.mkdir()
    assert git_common_dir(d) is None
    assert shared_embeddings_dir(d) == d.resolve() / ".repoctx" / "embeddings"


# -- shared index visible from any worktree (AC#1) ---------------------------


def test_index_built_from_main_is_found_from_worktree(tmp_path: Path) -> None:
    main = _init_repo(tmp_path / "main")
    wt = _add_worktree(main, tmp_path / "wt-a", "feat")
    # "Build" from the main checkout: save into its shared dir.
    _tiny_index().save(shared_embeddings_dir(main))
    # The worktree resolves to the same dir and can load it.
    loaded = VectorIndex.load(resolve_embeddings_dir(wt))
    assert len(loaded) == 2
    assert resolve_embeddings_dir(wt) == shared_embeddings_dir(main)


# -- migration (AC#6) --------------------------------------------------------


def test_migrates_legacy_in_tree_index_to_shared(tmp_path: Path) -> None:
    main = _init_repo(tmp_path / "main")
    legacy = legacy_embeddings_dir(main)
    _tiny_index().save(legacy)
    assert (legacy / "vectors.npy").exists()

    migrated = migrate_legacy_index_if_needed(main)
    assert migrated is True
    shared = shared_embeddings_dir(main)
    assert (shared / "vectors.npy").exists()
    assert not (legacy / "vectors.npy").exists()


def test_resolve_prefers_shared_and_migrates(tmp_path: Path) -> None:
    main = _init_repo(tmp_path / "main")
    _tiny_index().save(legacy_embeddings_dir(main))
    # Resolution should migrate then return the shared dir.
    resolved = resolve_embeddings_dir(main)
    assert resolved == shared_embeddings_dir(main)
    assert (resolved / "vectors.npy").exists()


def test_migration_noop_when_shared_already_present(tmp_path: Path) -> None:
    main = _init_repo(tmp_path / "main")
    _tiny_index().save(shared_embeddings_dir(main))
    _tiny_index().save(legacy_embeddings_dir(main))  # stale leftover
    # Shared wins; no migration, legacy left untouched.
    assert migrate_legacy_index_if_needed(main) is False
    assert resolve_embeddings_dir(main) == shared_embeddings_dir(main)


def test_no_migration_for_non_git_dir(tmp_path: Path) -> None:
    d = tmp_path / "plain"
    d.mkdir()
    _tiny_index().save(legacy_embeddings_dir(d))
    # shared == legacy here, so nothing to do.
    assert migrate_legacy_index_if_needed(d) is False
    assert resolve_embeddings_dir(d) == legacy_embeddings_dir(d)
