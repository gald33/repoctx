"""Tests for the per-repo feedback event log."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from repoctx.feedback_log import (
    FEEDBACK_DIR,
    FEEDBACK_FILENAME,
    append_event,
    find_recent_bundle_for_path,
    read_events,
)


def _read_lines(repo: Path) -> list[dict]:
    path = repo / FEEDBACK_DIR / FEEDBACK_FILENAME
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_append_event_creates_file_and_writes_jsonl(tmp_path: Path):
    append_event(tmp_path, {"event_type": "bundle_emitted", "bundle_id": "abc"})
    written = _read_lines(tmp_path)
    assert len(written) == 1
    assert written[0]["event_type"] == "bundle_emitted"
    assert written[0]["bundle_id"] == "abc"
    assert "schema_version" in written[0]
    assert "event_time" in written[0]


def test_append_event_preserves_existing_event_time(tmp_path: Path):
    custom_ts = "2026-05-14T09:00:00Z"
    append_event(tmp_path, {"event_type": "x", "event_time": custom_ts})
    assert _read_lines(tmp_path)[0]["event_time"] == custom_ts


def test_append_event_respects_feedback_disabled_flag(tmp_path: Path):
    (tmp_path / FEEDBACK_DIR).mkdir(parents=True)
    (tmp_path / FEEDBACK_DIR / "config.json").write_text(
        json.dumps({"feedback_enabled": False})
    )
    result = append_event(tmp_path, {"event_type": "bundle_emitted"})
    assert result is None
    log_path = tmp_path / FEEDBACK_DIR / FEEDBACK_FILENAME
    assert not log_path.exists()


def test_append_event_respects_env_disable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("REPOCTX_FEEDBACK_ENABLED", "0")
    result = append_event(tmp_path, {"event_type": "bundle_emitted"})
    assert result is None


def test_read_events_streams_in_order(tmp_path: Path):
    append_event(tmp_path, {"event_type": "bundle_emitted", "bundle_id": "a"})
    append_event(tmp_path, {"event_type": "tool_use", "bundle_id": "a", "path": "x.py"})
    events = list(read_events(tmp_path))
    assert [e["event_type"] for e in events] == ["bundle_emitted", "tool_use"]


def test_read_events_skips_malformed_lines(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    log_path = tmp_path / FEEDBACK_DIR / FEEDBACK_FILENAME
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        '{"event_type": "ok"}\n'
        "not-json-at-all\n"
        '{"event_type": "ok2"}\n'
    )
    with caplog.at_level("WARNING"):
        events = list(read_events(tmp_path))
    assert [e["event_type"] for e in events] == ["ok", "ok2"]


def test_read_events_missing_file_returns_empty(tmp_path: Path):
    assert list(read_events(tmp_path)) == []


def test_read_events_filters_by_since(tmp_path: Path):
    append_event(tmp_path, {"event_type": "a", "event_time": "2026-05-14T09:00:00Z"})
    append_event(tmp_path, {"event_type": "b", "event_time": "2026-05-14T10:00:00Z"})
    append_event(tmp_path, {"event_type": "c", "event_time": "2026-05-14T11:00:00Z"})
    events = list(read_events(tmp_path, since_iso="2026-05-14T10:00:00Z"))
    assert [e["event_type"] for e in events] == ["b", "c"]


def test_find_recent_bundle_attributes_to_most_recent_match(tmp_path: Path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    iso = lambda dt: dt.isoformat().replace("+00:00", "Z")
    append_event(tmp_path, {
        "event_type": "bundle_emitted",
        "bundle_id": "older",
        "ranked_paths": [{"path": "src/x.py", "kind": "code", "score": 0.5}],
        "event_time": iso(now - timedelta(minutes=10)),
    })
    append_event(tmp_path, {
        "event_type": "bundle_emitted",
        "bundle_id": "newer",
        "ranked_paths": [{"path": "src/x.py", "kind": "code", "score": 0.7}],
        "event_time": iso(now - timedelta(minutes=1)),
    })
    assert find_recent_bundle_for_path(tmp_path, "src/x.py", now_iso=iso(now)) == "newer"


def test_find_recent_bundle_returns_none_when_outside_window(tmp_path: Path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    iso = lambda dt: dt.isoformat().replace("+00:00", "Z")
    append_event(tmp_path, {
        "event_type": "bundle_emitted",
        "bundle_id": "stale",
        "ranked_paths": [{"path": "src/x.py", "kind": "code", "score": 0.5}],
        "event_time": iso(now - timedelta(hours=2)),  # outside default 30-min window
    })
    assert find_recent_bundle_for_path(tmp_path, "src/x.py", now_iso=iso(now)) is None


def test_find_recent_bundle_returns_none_for_unbundled_path(tmp_path: Path):
    append_event(tmp_path, {
        "event_type": "bundle_emitted",
        "bundle_id": "a",
        "ranked_paths": [{"path": "src/x.py", "kind": "code", "score": 0.5}],
    })
    assert find_recent_bundle_for_path(tmp_path, "src/y.py") is None
