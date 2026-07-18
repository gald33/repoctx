"""Tests for repoctx.reporting — upload state, queue, transport, disclosure."""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from repoctx import reporting
from repoctx.reporting import (
    PostResult,
    ReportingState,
    build_upload_payload,
    compute_repo_fingerprint,
    enqueue_if_enabled,
    flush,
    get_install_id,
    get_queued_events,
    get_status,
    is_enabled,
    load_state,
    maybe_show_canary_notice,
    purge_queue,
    save_state,
    set_enabled,
)


# ---- Fixtures ---------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset module-level caches between tests + scrub env vars."""
    reporting.reset_for_test()
    monkeypatch.delenv("REPOCTX_REPORTING", raising=False)
    monkeypatch.delenv("REPOCTX_REPORTING_DIR", raising=False)
    monkeypatch.delenv("REPOCTX_REPORTING_ENDPOINT", raising=False)
    monkeypatch.delenv("REPOCTX_DOGFOOD", raising=False)
    # Endpoint falls back to the real production URL once the override is
    # gone, so keep the enqueue-path autoflush off. Tests that exercise it
    # turn it on explicitly and stub the transport.
    monkeypatch.setenv("REPOCTX_REPORTING_AUTOFLUSH", "off")


@pytest.fixture
def stable_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reporting, "CHANNEL", "stable")


@pytest.fixture
def canary_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reporting, "CHANNEL", "canary")


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "state"


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a real git repo with one commit (per repoctx test convention)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "--allow-empty", "-m", "init"],
        cwd=repo,
        check=True,
    )
    return repo


# ---- Channel defaults -------------------------------------------------------


def test_stable_default_is_off(stable_channel: None, state_dir: Path) -> None:
    """Stable channel: silent default-off, no first-run prompt, no questions."""
    assert is_enabled(state_dir=state_dir) is False


def test_stable_is_enabled_does_not_create_state_file(
    stable_channel: None, state_dir: Path
) -> None:
    """Critical: a stable install that never opts in must leave no trace.

    If a user installs repoctx on the stable channel and never touches
    reporting, calling is_enabled() (which happens on every protocol op via
    enqueue_if_enabled) MUST NOT create ~/.repoctx/reporting.json.
    """
    assert is_enabled(state_dir=state_dir) is False
    assert not (state_dir / "reporting.json").exists()
    assert not state_dir.exists() or list(state_dir.iterdir()) == []


def test_canary_default_is_on(canary_channel: None, state_dir: Path) -> None:
    """Canary channel: default-on, with disclosure (tested separately)."""
    assert is_enabled(state_dir=state_dir) is True


def test_state_file_explicit_value_wins_over_channel_default(
    canary_channel: None, state_dir: Path
) -> None:
    set_enabled(False, state_dir=state_dir)
    assert is_enabled(state_dir=state_dir) is False


def test_env_var_overrides_state_file(
    canary_channel: None,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_enabled(True, state_dir=state_dir)
    monkeypatch.setenv("REPOCTX_REPORTING", "off")
    assert is_enabled(state_dir=state_dir) is False


def test_env_var_can_force_on(
    stable_channel: None,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REPOCTX_REPORTING", "on")
    assert is_enabled(state_dir=state_dir) is True


@pytest.mark.parametrize("value", ["off", "0", "false", "no", "OFF", "False"])
def test_env_var_off_synonyms(
    canary_channel: None,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("REPOCTX_REPORTING", value)
    assert is_enabled(state_dir=state_dir) is False


# ---- Install ID -------------------------------------------------------------


def test_install_id_persists_across_load_calls(state_dir: Path) -> None:
    first = get_install_id(state_dir=state_dir)
    reporting.reset_for_test()  # clear cache to force re-read
    second = get_install_id(state_dir=state_dir)
    assert first == second
    assert len(first) > 0


def test_install_id_format_is_uuid_like(state_dir: Path) -> None:
    install_id = get_install_id(state_dir=state_dir)
    # UUID4 string form is 36 chars with 4 hyphens
    assert len(install_id) == 36
    assert install_id.count("-") == 4


# ---- State file resilience --------------------------------------------------


def test_corrupted_state_file_is_recovered(state_dir: Path) -> None:
    state_dir.mkdir(parents=True)
    (state_dir / "reporting.json").write_text("{ this is not valid json")
    state = load_state(state_dir=state_dir)
    assert isinstance(state.install_id, str)
    assert len(state.install_id) > 0


def test_state_file_round_trip(state_dir: Path) -> None:
    state = load_state(state_dir=state_dir)
    state.enabled = True
    state.endpoint = "https://example.com/v1/events"
    save_state(state, state_dir=state_dir)

    reloaded = load_state(state_dir=state_dir)
    assert reloaded.install_id == state.install_id
    assert reloaded.enabled is True
    assert reloaded.endpoint == "https://example.com/v1/events"


# ---- Repo fingerprint -------------------------------------------------------


def test_repo_fingerprint_stable_for_same_install_and_repo(
    state_dir: Path, git_repo: Path
) -> None:
    fp1 = compute_repo_fingerprint(git_repo, state_dir=state_dir)
    fp2 = compute_repo_fingerprint(git_repo, state_dir=state_dir)
    assert fp1 == fp2
    assert fp1 is not None
    assert len(fp1) == 64  # sha256 hex


def test_repo_fingerprint_differs_per_install(
    tmp_path: Path, git_repo: Path
) -> None:
    state_a = tmp_path / "state_a"
    state_b = tmp_path / "state_b"
    reporting.reset_for_test()
    fp_a = compute_repo_fingerprint(git_repo, state_dir=state_a)
    reporting.reset_for_test()
    fp_b = compute_repo_fingerprint(git_repo, state_dir=state_b)
    assert fp_a is not None
    assert fp_b is not None
    assert fp_a != fp_b


def test_repo_fingerprint_none_for_non_git_dir(
    state_dir: Path, tmp_path: Path
) -> None:
    non_git = tmp_path / "plain"
    non_git.mkdir()
    fp = compute_repo_fingerprint(non_git, state_dir=state_dir)
    assert fp is None


def test_repo_fingerprint_none_for_no_repo(state_dir: Path) -> None:
    assert compute_repo_fingerprint(None, state_dir=state_dir) is None


# ---- Payload construction ---------------------------------------------------


def test_build_upload_payload_strips_forbidden_keys(
    canary_channel: None, state_dir: Path
) -> None:
    local = {
        "event_type": "protocol_op",
        "event_time": "2026-05-26T12:00:00Z",
        "op": "bundle",
        "success": True,
        "duration_ms": 100,
        "query_hash": "abc123",       # forbidden — leaks structure across users
        "repo_hash": "deadbeef",       # forbidden — replaced by repo_fingerprint
        "task_hash": "xxxx",           # forbidden
        "repo_root": "/Users/alice/proj",  # forbidden
        "query": "refactor auth",      # forbidden
    }
    upload = build_upload_payload(local, state_dir=state_dir)

    for forbidden in ("query_hash", "repo_hash", "task_hash", "repo_root", "query"):
        assert forbidden not in upload, f"{forbidden} leaked into upload payload"

    assert upload["op"] == "bundle"
    assert upload["success"] is True
    assert upload["channel"] == "canary"
    assert "install_id" in upload
    assert "build_id" in upload
    assert upload["upload_schema_version"] == 1


def test_build_upload_payload_adds_repo_fingerprint_for_git_repo(
    state_dir: Path, git_repo: Path
) -> None:
    local = {"event_type": "protocol_op", "op": "bundle"}
    upload = build_upload_payload(local, repo_root=git_repo, state_dir=state_dir)
    assert "repo_fingerprint" in upload
    assert len(upload["repo_fingerprint"]) == 64


def test_build_upload_payload_omits_repo_fingerprint_without_git(
    state_dir: Path, tmp_path: Path
) -> None:
    non_git = tmp_path / "plain"
    non_git.mkdir()
    local = {"event_type": "protocol_op", "op": "bundle"}
    upload = build_upload_payload(local, repo_root=non_git, state_dir=state_dir)
    assert "repo_fingerprint" not in upload


# ---- Opportunistic flush ----------------------------------------------------
#
# Regression cover for the bug that made the whole upload lane dead weight:
# the only automatic flush was an atexit hook, and the MCP server — a
# long-lived process killed with SIGTERM — never runs atexit handlers. Events
# queued forever (215 events / 6 weeks / 0 uploads observed in the wild).


@pytest.fixture
def _autoflush_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPOCTX_REPORTING_AUTOFLUSH", "1")


def _stub_flush(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Replace the real flush with a recorder; returns the call log."""
    calls: list[int] = []

    def fake_flush(**kwargs: Any) -> PostResult:
        calls.append(1)
        return PostResult(sent=1, accepted=1, rejected=0, error=None)

    monkeypatch.setattr(reporting, "flush", fake_flush)
    return calls


def _wait_for(predicate, timeout: float = 2.0) -> bool:
    import time as _time

    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        if predicate():
            return True
        _time.sleep(0.01)
    return predicate()


def test_enqueue_triggers_background_flush(
    canary_channel: None, state_dir: Path, _autoflush_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_flush(monkeypatch)
    enqueue_if_enabled({"event_type": "protocol_op", "op": "bundle"}, state_dir=state_dir)
    assert _wait_for(lambda: len(calls) >= 1), "enqueue did not trigger a flush"


def test_autoflush_can_be_disabled(
    canary_channel: None, state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REPOCTX_REPORTING_AUTOFLUSH", "off")
    calls = _stub_flush(monkeypatch)
    enqueue_if_enabled({"event_type": "protocol_op", "op": "bundle"}, state_dir=state_dir)
    import time as _time

    _time.sleep(0.2)
    assert calls == [], "autoflush should be inert when disabled"


def test_maybe_flush_async_noop_when_reporting_disabled(
    stable_channel: None, state_dir: Path, _autoflush_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_flush(monkeypatch)
    # Stable + no opt-in => disabled; a stray queue file must not upload.
    assert reporting.maybe_flush_async(state_dir, queue_bytes=999_999) is False
    assert calls == []


def test_maybe_flush_async_skips_when_queue_empty(
    canary_channel: None, state_dir: Path, _autoflush_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_flush(monkeypatch)
    assert reporting.maybe_flush_async(state_dir, queue_bytes=0) is False
    assert calls == []


def test_maybe_flush_async_single_flights(
    canary_channel: None, state_dir: Path, _autoflush_on: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A burst of enqueues must not spawn a thread each."""
    import threading as _threading

    release = _threading.Event()
    started = []

    def slow_flush(**kwargs: Any) -> PostResult:
        started.append(1)
        release.wait(timeout=2.0)
        return PostResult(sent=1, accepted=1, rejected=0, error=None)

    monkeypatch.setattr(reporting, "flush", slow_flush)
    assert reporting.maybe_flush_async(state_dir, queue_bytes=999_999) is True
    assert _wait_for(lambda: len(started) == 1)
    # Second call while the first is in flight is refused.
    assert reporting.maybe_flush_async(state_dir, queue_bytes=999_999) is False
    release.set()


# ---- Dogfood mode -----------------------------------------------------------


def test_is_dogfood_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    assert reporting.is_dogfood() is False
    monkeypatch.setenv("REPOCTX_DOGFOOD", "1")
    assert reporting.is_dogfood() is True
    monkeypatch.setenv("REPOCTX_DOGFOOD", "off")
    assert reporting.is_dogfood() is False


def test_dogfood_implies_enabled_on_stable(
    stable_channel: None, state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Stable defaults OFF...
    assert is_enabled(state_dir) is False
    # ...but dogfood forces it on without touching the state file.
    monkeypatch.setenv("REPOCTX_DOGFOOD", "1")
    assert is_enabled(state_dir) is True


def test_reporting_kill_switch_beats_dogfood(
    stable_channel: None, state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REPOCTX_DOGFOOD", "1")
    monkeypatch.setenv("REPOCTX_REPORTING", "off")
    assert is_enabled(state_dir) is False


def test_build_upload_payload_keeps_detail_in_dogfood(
    stable_channel: None, state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REPOCTX_DOGFOOD", "1")
    local = {
        "event_type": "protocol_op",
        "op": "bundle",
        "success": False,
        "error_type": "RuntimeError",
        "error_message": "boom while bundling",
        "traceback": 'Traceback (most recent call last):\n  ...\nRuntimeError: boom',
        "repo_root": "/Users/alice/proj",  # still forbidden, even in dogfood
        "query": "refactor auth",           # still forbidden
    }
    upload = build_upload_payload(local, state_dir=state_dir)

    assert upload["dogfood"] is True
    assert upload["error_message"] == "boom while bundling"
    assert "RuntimeError: boom" in upload["traceback"]
    # The genuinely sensitive keys are stripped even here.
    assert "repo_root" not in upload
    assert "query" not in upload


def test_build_upload_payload_strips_detail_without_dogfood(
    stable_channel: None, state_dir: Path
) -> None:
    local = {
        "event_type": "protocol_op",
        "op": "bundle",
        "success": False,
        "error_type": "RuntimeError",
        "error_message": "boom while bundling",
        "traceback": "Traceback ...\nRuntimeError: boom",
    }
    upload = build_upload_payload(local, state_dir=state_dir)

    assert "dogfood" not in upload
    assert "error_message" not in upload
    assert "traceback" not in upload
    assert upload["error_type"] == "RuntimeError"  # class survives, as before


def test_capture_exc_detail_only_in_dogfood(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    try:
        raise ValueError("something specific broke")
    except ValueError as exc:
        msg_off, tb_off = reporting.capture_exc_detail(exc)
        assert msg_off is None and tb_off is None

        monkeypatch.setenv("REPOCTX_DOGFOOD", "1")
        msg_on, tb_on = reporting.capture_exc_detail(exc)
        assert msg_on == "something specific broke"
        assert tb_on is not None and "ValueError: something specific broke" in tb_on


def test_capture_exc_detail_truncates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPOCTX_DOGFOOD", "1")
    try:
        raise ValueError("x" * 10_000)
    except ValueError as exc:
        msg, tb = reporting.capture_exc_detail(exc)
    assert msg is not None and len(msg) == reporting.DOGFOOD_MAX_MESSAGE_CHARS
    assert tb is not None and len(tb) <= reporting.DOGFOOD_MAX_TRACEBACK_CHARS


def test_status_reports_dogfood(
    stable_channel: None, state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REPOCTX_DOGFOOD", "1")
    status = get_status(state_dir)
    assert status["dogfood"] is True
    assert status["enabled"] is True
    assert status["enabled_source"] == "dogfood"


# ---- Enqueue ----------------------------------------------------------------


def test_enqueue_no_op_when_disabled(stable_channel: None, state_dir: Path) -> None:
    result = enqueue_if_enabled(
        {"event_type": "protocol_op", "op": "bundle"},
        state_dir=state_dir,
    )
    assert result is False
    assert get_queued_events(state_dir=state_dir) == []


def test_enqueue_writes_when_enabled(canary_channel: None, state_dir: Path) -> None:
    result = enqueue_if_enabled(
        {"event_type": "protocol_op", "op": "bundle", "success": True},
        state_dir=state_dir,
    )
    assert result is True
    queued = get_queued_events(state_dir=state_dir)
    assert len(queued) == 1
    assert queued[0]["op"] == "bundle"
    assert queued[0]["channel"] == "canary"
    # Forbidden keys must never appear in the queue either
    assert "query" not in queued[0]
    assert "repo_hash" not in queued[0]


def test_enqueue_drops_oldest_at_cap(canary_channel: None, state_dir: Path) -> None:
    # Tighten the cap so the test is fast
    state = load_state(state_dir=state_dir)
    state.max_queue_bytes = 500  # small enough to force eviction quickly
    save_state(state, state_dir=state_dir)

    for i in range(50):
        enqueue_if_enabled(
            {"event_type": "protocol_op", "op": "bundle", "seq": i},
            state_dir=state_dir,
        )

    queued = get_queued_events(limit=1000, state_dir=state_dir)
    assert len(queued) >= 1
    assert len(queued) < 50, "Cap did not evict any events"
    # Newest events must survive (eviction is from the front)
    last_seq = queued[-1]["seq"]
    assert last_seq == 49


def test_purge_queue_clears(canary_channel: None, state_dir: Path) -> None:
    enqueue_if_enabled({"event_type": "protocol_op", "op": "bundle"}, state_dir=state_dir)
    assert len(get_queued_events(state_dir=state_dir)) == 1
    purged = purge_queue(state_dir=state_dir)
    assert purged > 0
    assert get_queued_events(state_dir=state_dir) == []


# ---- Canary disclosure ------------------------------------------------------


def test_canary_notice_shown_once(canary_channel: None, state_dir: Path) -> None:
    stream = io.StringIO()
    assert maybe_show_canary_notice(state_dir=state_dir, stream=stream) is True
    assert "canary" in stream.getvalue()
    assert "reporting off" in stream.getvalue()

    # Second call is a no-op
    stream2 = io.StringIO()
    assert maybe_show_canary_notice(state_dir=state_dir, stream=stream2) is False
    assert stream2.getvalue() == ""


def test_stable_never_shows_notice(stable_channel: None, state_dir: Path) -> None:
    stream = io.StringIO()
    assert maybe_show_canary_notice(state_dir=state_dir, stream=stream) is False
    assert stream.getvalue() == ""


# ---- Flush ------------------------------------------------------------------


class _MemoryPoster:
    """Test double for Poster — records what was posted, never hits network."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[list[dict[str, Any]]] = []
        self.fail = fail

    def post(self, events: list[dict[str, Any]]) -> PostResult:
        self.calls.append(events)
        if self.fail:
            return PostResult(sent=0, accepted=None, rejected=None, error="simulated")
        return PostResult(sent=len(events), accepted=len(events), rejected=0, error=None)


def test_flush_drains_queue_on_success(
    canary_channel: None, state_dir: Path
) -> None:
    for i in range(3):
        enqueue_if_enabled(
            {"event_type": "protocol_op", "op": "bundle", "seq": i},
            state_dir=state_dir,
        )
    poster = _MemoryPoster()
    result = flush(poster=poster, state_dir=state_dir)

    assert result.error is None
    assert result.sent == 3
    assert len(poster.calls) == 1
    assert len(poster.calls[0]) == 3
    assert get_queued_events(state_dir=state_dir) == []


def test_flush_keeps_queue_on_failure(
    canary_channel: None, state_dir: Path
) -> None:
    enqueue_if_enabled({"event_type": "protocol_op", "op": "bundle"}, state_dir=state_dir)
    poster = _MemoryPoster(fail=True)
    result = flush(poster=poster, state_dir=state_dir)

    assert result.error == "simulated"
    assert get_queued_events(state_dir=state_dir), "Queue must be preserved on failure for retry"


def test_flush_no_op_when_disabled(stable_channel: None, state_dir: Path) -> None:
    # Pre-populate the queue manually (bypass enqueue_if_enabled's guard)
    state_dir.mkdir(parents=True)
    queue_dir = state_dir / "reporting"
    queue_dir.mkdir()
    queue_path = queue_dir / "queue.jsonl"
    queue_path.write_text('{"event_type":"protocol_op","op":"bundle"}\n')

    poster = _MemoryPoster()
    result = flush(poster=poster, state_dir=state_dir)

    assert result.error is None
    assert result.sent == 0
    assert poster.calls == []
    # Queue is preserved — flush doesn't double as purge when disabled
    assert queue_path.exists()


# ---- Status -----------------------------------------------------------------


def test_status_reports_effective_state(
    canary_channel: None, state_dir: Path
) -> None:
    enqueue_if_enabled({"event_type": "protocol_op", "op": "bundle"}, state_dir=state_dir)
    status = get_status(state_dir=state_dir)

    assert status["channel"] == "canary"
    assert status["enabled"] is True
    assert status["enabled_source"] == "channel_default"
    assert status["channel_default"] is True
    assert status["queue_bytes"] > 0
    assert "install_id" in status


def test_status_reports_env_source(
    canary_channel: None,
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REPOCTX_REPORTING", "off")
    status = get_status(state_dir=state_dir)
    assert status["enabled"] is False
    assert status["enabled_source"] == "env"
