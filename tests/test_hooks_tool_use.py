"""Tests for the PostToolUse tool-use feedback hook handler."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from repoctx.feedback_log import append_event, read_events
from repoctx.hooks import handle_tool_use


def _init_git_repo(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)


def test_tracked_tool_appends_event(tmp_path: Path):
    _init_git_repo(tmp_path)
    file_path = tmp_path / "src" / "x.py"
    file_path.parent.mkdir(parents=True)
    file_path.write_text("# x\n")

    out = handle_tool_use(
        {
            "tool_name": "Read",
            "tool_input": {"file_path": str(file_path)},
            "cwd": str(tmp_path),
        },
        cwd=str(tmp_path),
    )
    # Silent hook
    assert out.stdout == ""
    assert out.stderr == ""

    events = list(read_events(tmp_path))
    assert len(events) == 1
    e = events[0]
    assert e["event_type"] == "tool_use"
    assert e["action"] == "Read"
    assert e["source"] == "hook"
    assert e["path"] == "src/x.py"
    # No matching bundle in the log, so bundle_id is None
    assert e["bundle_id"] is None


def test_untracked_tool_writes_nothing(tmp_path: Path):
    _init_git_repo(tmp_path)
    handle_tool_use(
        {"tool_name": "Bash", "tool_input": {"file_path": "/x"}, "cwd": str(tmp_path)},
        cwd=str(tmp_path),
    )
    assert list(read_events(tmp_path)) == []


def test_attribution_resolves_recent_bundle(tmp_path: Path):
    _init_git_repo(tmp_path)
    file_path = tmp_path / "src" / "x.py"
    file_path.parent.mkdir(parents=True)
    file_path.write_text("# x\n")

    # Pre-seed a bundle that contains this path.
    append_event(tmp_path, {
        "event_type": "bundle_emitted",
        "bundle_id": "bundle-XYZ",
        "ranked_paths": [{"path": "src/x.py", "kind": "code", "score": 0.6}],
    })

    handle_tool_use(
        {
            "tool_name": "Edit",
            "tool_input": {"file_path": str(file_path)},
            "cwd": str(tmp_path),
        },
        cwd=str(tmp_path),
    )

    events = [e for e in read_events(tmp_path) if e["event_type"] == "tool_use"]
    assert len(events) == 1
    assert events[0]["bundle_id"] == "bundle-XYZ"
    assert events[0]["path"] == "src/x.py"


def test_missing_file_path_is_silent(tmp_path: Path):
    _init_git_repo(tmp_path)
    handle_tool_use(
        {"tool_name": "Read", "tool_input": {}, "cwd": str(tmp_path)},
        cwd=str(tmp_path),
    )
    assert list(read_events(tmp_path)) == []


def test_path_outside_repo_is_skipped(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    other = tmp_path / "other"
    other.mkdir()
    outside = other / "z.py"
    outside.write_text("# z\n")

    handle_tool_use(
        {
            "tool_name": "Read",
            "tool_input": {"file_path": str(outside)},
            "cwd": str(repo),
        },
        cwd=str(repo),
    )
    # The path doesn't live under the repo, so no event for the repo.
    assert list(read_events(repo)) == []


def test_hook_swallows_broken_log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _init_git_repo(tmp_path)
    file_path = tmp_path / "x.py"
    file_path.write_text("# x\n")
    # Make the feedback dir unwriteable by replacing it with a regular file.
    bad = tmp_path / ".repoctx"
    bad.write_text("not a dir")
    out = handle_tool_use(
        {
            "tool_name": "Read",
            "tool_input": {"file_path": str(file_path)},
            "cwd": str(tmp_path),
        },
        cwd=str(tmp_path),
    )
    # Should not raise; output is silent.
    assert out.stdout == ""
    assert out.stderr == ""
