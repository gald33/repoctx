"""Tests for the git-diff feedback reaper."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from repoctx.feedback_log import append_event, read_events
from repoctx.reaper import reap


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _init_repo_with_baseline(repo: Path) -> None:
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("# a\n")
    (repo / "src" / "b.py").write_text("# b\n")
    (repo / "src" / "c.py").write_text("# c\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init", "--allow-empty")


def test_no_bundles_means_no_work(tmp_path: Path):
    _init_repo_with_baseline(tmp_path)
    summary = reap(tmp_path)
    assert summary["bundles_scanned"] == 0
    assert summary["edits_emitted"] == 0


def test_emits_git_edit_for_modified_bundle_path(tmp_path: Path):
    _init_repo_with_baseline(tmp_path)
    append_event(tmp_path, {
        "event_type": "bundle_emitted",
        "bundle_id": "b1",
        "ranked_paths": [
            {"path": "src/a.py", "kind": "code", "score": 0.5},
            {"path": "src/b.py", "kind": "code", "score": 0.4},
            {"path": "src/c.py", "kind": "code", "score": 0.3},
        ],
    })
    # Modify a.py only.
    (tmp_path / "src" / "a.py").write_text("# a edited\n")

    summary = reap(tmp_path)
    assert summary["bundles_scanned"] == 1
    assert summary["edits_emitted"] == 1
    assert summary["worktrees_checked"] >= 1

    git_edits = [e for e in read_events(tmp_path) if e["event_type"] == "git_edit"]
    assert len(git_edits) == 1
    assert git_edits[0]["bundle_id"] == "b1"
    assert git_edits[0]["path"] == "src/a.py"
    assert git_edits[0]["source"] == "git"


def test_ignores_paths_not_in_bundle(tmp_path: Path):
    _init_repo_with_baseline(tmp_path)
    append_event(tmp_path, {
        "event_type": "bundle_emitted",
        "bundle_id": "b1",
        "ranked_paths": [{"path": "src/a.py", "kind": "code", "score": 0.5}],
    })
    # Modify b.py which is NOT in the bundle.
    (tmp_path / "src" / "b.py").write_text("# b edited\n")

    summary = reap(tmp_path)
    assert summary["edits_emitted"] == 0
    git_edits = [e for e in read_events(tmp_path) if e["event_type"] == "git_edit"]
    assert git_edits == []


def test_reap_is_idempotent(tmp_path: Path):
    _init_repo_with_baseline(tmp_path)
    append_event(tmp_path, {
        "event_type": "bundle_emitted",
        "bundle_id": "b1",
        "ranked_paths": [{"path": "src/a.py", "kind": "code", "score": 0.5}],
    })
    (tmp_path / "src" / "a.py").write_text("# edited\n")

    reap(tmp_path)
    second = reap(tmp_path)
    # First run emitted one git_edit; second run should not re-emit.
    assert second["edits_emitted"] == 0
    git_edits = [e for e in read_events(tmp_path) if e["event_type"] == "git_edit"]
    assert len(git_edits) == 1


def test_untracked_file_counts_as_edit(tmp_path: Path):
    _init_repo_with_baseline(tmp_path)
    append_event(tmp_path, {
        "event_type": "bundle_emitted",
        "bundle_id": "b1",
        "ranked_paths": [{"path": "src/d.py", "kind": "code", "score": 0.4}],
    })
    # Create an untracked file that's in the bundle's ranked paths.
    (tmp_path / "src" / "d.py").write_text("# new\n")

    summary = reap(tmp_path)
    assert summary["edits_emitted"] == 1
    git_edits = [e for e in read_events(tmp_path) if e["event_type"] == "git_edit"]
    assert git_edits[0]["path"] == "src/d.py"


def test_non_git_repo_still_runs(tmp_path: Path):
    # No git init — the reaper should fall back to repo_root as the only worktree
    # and still scan bundles. Without git, edits_emitted will be zero.
    append_event(tmp_path, {
        "event_type": "bundle_emitted",
        "bundle_id": "b1",
        "ranked_paths": [{"path": "x.py", "kind": "code", "score": 0.5}],
    })
    summary = reap(tmp_path)
    # No crash; bundles_scanned reflects the open bundle.
    assert summary["bundles_scanned"] == 1
    assert summary["edits_emitted"] == 0
