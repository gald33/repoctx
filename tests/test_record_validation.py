"""Tests for the record_validation op."""
from __future__ import annotations

from pathlib import Path

from repoctx.feedback_log import read_events
from repoctx.ops import op_record_validation


def _runs(repo: Path):
    return [e for e in read_events(repo) if e.get("event_type") == "validation_run"]


def test_records_one_event_per_run(tmp_path: Path):
    out = op_record_validation("b1", [
        {"command": "pytest -q", "exit_code": 0},
        {"command": "ruff check .", "exit_code": 1},
    ], repo_root=tmp_path)
    assert out["recorded"] == 2 and out["passed"] == 1 and out["failed"] == 1
    events = _runs(tmp_path)
    assert {e["command"] for e in events} == {"pytest -q", "ruff check ."}
    assert [e["passed"] for e in events if e["command"] == "ruff check ."] == [False]


def test_passed_is_derived_not_trusted(tmp_path: Path):
    """A caller claiming passed=True on a nonzero exit must not be believed."""
    op_record_validation("b1", [
        {"command": "pytest -q", "exit_code": 2, "passed": True},
    ], repo_root=tmp_path)
    assert _runs(tmp_path)[0]["passed"] is False


def test_bad_entries_are_skipped_not_fatal(tmp_path: Path):
    out = op_record_validation("b1", [
        {"command": "pytest -q", "exit_code": 0},
        {"command": "", "exit_code": 0},
        {"command": "x", "exit_code": "0"},
        {"command": "y", "exit_code": True},
        "not-a-dict",
    ], repo_root=tmp_path)
    assert out["recorded"] == 1
    assert out["skipped"] == 4
    assert len(_runs(tmp_path)) == 1


def test_missing_bundle_id_is_an_error_not_a_crash(tmp_path: Path):
    out = op_record_validation("", [{"command": "pytest", "exit_code": 0}], repo_root=tmp_path)
    assert out["recorded"] == 0 and "error" in out
    assert _runs(tmp_path) == []


def test_non_list_runs_is_an_error(tmp_path: Path):
    out = op_record_validation("b1", {"command": "pytest"}, repo_root=tmp_path)  # type: ignore[arg-type]
    assert out["recorded"] == 0 and "error" in out


def test_long_command_is_truncated(tmp_path: Path):
    from repoctx.ops.record_validation import MAX_COMMAND_CHARS
    op_record_validation("b1", [{"command": "x" * 5000, "exit_code": 0}], repo_root=tmp_path)
    assert len(_runs(tmp_path)[0]["command"]) == MAX_COMMAND_CHARS
