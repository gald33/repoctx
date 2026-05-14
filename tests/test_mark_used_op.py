"""Tests for the mark_used op."""

from __future__ import annotations

from pathlib import Path

import pytest

from repoctx.feedback_log import FEEDBACK_DIR, FEEDBACK_FILENAME, read_events
from repoctx.ops.mark_used import op_mark_used


def test_records_each_valid_label(tmp_path: Path):
    result = op_mark_used(
        "bundle-1",
        [
            {"path": "src/a.py", "relevance": "informed_edit"},
            {"path": "src/b.py", "relevance": "informed_context"},
            {"path": "src/c.py", "relevance": "noise"},
        ],
        repo_root=tmp_path,
    )
    assert result == {"recorded": 3, "skipped": 0, "bundle_id": "bundle-1"}
    events = list(read_events(tmp_path))
    assert [e["relevance"] for e in events] == ["informed_edit", "informed_context", "noise"]
    assert all(e["bundle_id"] == "bundle-1" for e in events)
    assert all(e["source"] == "self_report" for e in events)


def test_rejects_empty_bundle_id(tmp_path: Path):
    result = op_mark_used("", [{"path": "x", "relevance": "noise"}], repo_root=tmp_path)
    assert result["recorded"] == 0
    assert "error" in result


def test_skips_invalid_relevance(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    with caplog.at_level("WARNING"):
        result = op_mark_used(
            "b",
            [
                {"path": "ok.py", "relevance": "informed_edit"},
                {"path": "bad.py", "relevance": "very_useful"},
                {"path": "ok2.py", "relevance": "noise"},
            ],
            repo_root=tmp_path,
        )
    assert result == {"recorded": 2, "skipped": 1, "bundle_id": "b"}
    events = list(read_events(tmp_path))
    assert [e["path"] for e in events] == ["ok.py", "ok2.py"]


def test_skips_missing_path(tmp_path: Path):
    result = op_mark_used(
        "b",
        [
            {"relevance": "noise"},
            {"path": "", "relevance": "noise"},
            {"path": "x.py", "relevance": "informed_edit"},
        ],
        repo_root=tmp_path,
    )
    assert result == {"recorded": 1, "skipped": 2, "bundle_id": "b"}


def test_skips_non_dict_entries(tmp_path: Path):
    result = op_mark_used(
        "b",
        [
            "not a dict",
            ["also not"],
            {"path": "x.py", "relevance": "informed_edit"},
        ],
        repo_root=tmp_path,
    )
    assert result == {"recorded": 1, "skipped": 2, "bundle_id": "b"}


def test_labels_not_a_list(tmp_path: Path):
    result = op_mark_used("b", "not a list", repo_root=tmp_path)  # type: ignore[arg-type]
    assert result["recorded"] == 0
    assert "error" in result
