"""Per-op telemetry emission on the v2 *CLI* surface.

Mirrors ``tests/test_protocol_telemetry.py`` (which covers the MCP surface).
Before this, the CLI protocol-op entrypoints in
``repoctx.commands.protocol_ops`` produced zero telemetry, so all CLI usage
was invisible to the reporting/ingest pipeline and the per-repo retrieval
tuner. These tests pin the ``surface="cli"`` events — including the
dogfood-only failure detail — in place.

NOTE: ``tests/conftest.py`` hard-disables reporting suite-wide, so the
failure-detail test opts back into dogfood explicitly.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from repoctx.commands import protocol_ops


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "AGENTS.md").write_text("# Agents\n")
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (contracts / "tokens.md").write_text("# Token contract\nNever persist tokens.\n")
    src = tmp_path / "app"
    src.mkdir()
    (src / "tokens.py").write_text(
        "# INVARIANT: tokens must not be persisted\ndef t():\n    return 1\n"
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_tokens.py").write_text("def test_tokens():\n    assert True\n")
    return tmp_path


@pytest.fixture()
def telemetry_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point telemetry at a scratch dir the CLI paths pick up by default.

    The CLI ops don't thread a ``telemetry_dir`` argument, so they resolve it
    from ``REPOCTX_TELEMETRY_DIR`` (falling back to ``~/.repoctx/telemetry``);
    pinning the env keeps the suite from writing to a real home dir.
    """
    tdir = tmp_path / ".telemetry"
    monkeypatch.setenv("REPOCTX_TELEMETRY_DIR", str(tdir))
    return tdir


def _protocol_ops(telemetry_dir: Path) -> list[dict]:
    events_path = telemetry_dir / "repoctx-events.jsonl"
    if not events_path.exists():
        return []
    lines = events_path.read_text(encoding="utf-8").splitlines()
    return [
        json.loads(l)
        for l in lines
        if l.strip() and json.loads(l).get("event_type") == "protocol_op"
    ]


def test_cli_scope_records_protocol_op(repo: Path, telemetry_dir: Path) -> None:
    args = argparse.Namespace(task="tokens", repo=str(repo))
    protocol_ops.scope_cmd.run(args)

    ops = _protocol_ops(telemetry_dir)
    assert ops, "expected a protocol_op event on the CLI surface"
    ev = ops[-1]
    assert ev["op"] == "scope"
    assert ev["surface"] == "cli"
    assert ev["success"] is True
    assert ev["duration_ms"] >= 0
    assert ev["output_bytes"] > 0
    assert "task_hash" in ev and "repo_hash" in ev
    assert "task" not in ev, "raw task must never be persisted"
    # Dogfood-only detail stays absent off dogfood.
    assert "error_message" not in ev and "traceback" not in ev


@pytest.mark.parametrize(
    ("run", "args", "op_name"),
    [
        (
            protocol_ops.bundle_cmd.run,
            dict(task="tokens", full=False, include_advisory=False, format="json"),
            "bundle",
        ),
        (
            protocol_ops.authority_cmd.run,
            dict(task="tokens", include="summary"),
            "authority",
        ),
        (
            protocol_ops.validate_plan_cmd.run,
            dict(task="tokens", changed=["app/tokens.py"]),
            "validate_plan",
        ),
        (
            protocol_ops.risk_report_cmd.run,
            dict(task="tokens", changed=["contracts/tokens.md"]),
            "risk_report",
        ),
        (
            protocol_ops.refresh_cmd.run,
            dict(
                task="tokens",
                changed=["app/tokens.py"],
                current_scope_json=None,
                claude_md_nudge=False,
            ),
            "refresh",
        ),
    ],
)
def test_cli_ops_record_success(
    repo: Path, telemetry_dir: Path, run, args: dict, op_name: str
) -> None:
    run(argparse.Namespace(repo=str(repo), **args))

    ops = _protocol_ops(telemetry_dir)
    assert ops, f"expected a protocol_op event for {op_name}"
    ev = ops[-1]
    assert ev["op"] == op_name
    assert ev["surface"] == "cli"
    assert ev["success"] is True
    assert ev["output_bytes"] > 0


def test_cli_detect_changes_records_taskless_op(repo: Path, telemetry_dir: Path) -> None:
    # detect-changes carries no task; the op name still uses the MCP-shared
    # underscore form so CLI and MCP events aggregate together.
    args = argparse.Namespace(repo=str(repo), changed=["app/tokens.py"])
    protocol_ops.detect_changes_cmd.run(args)

    ops = _protocol_ops(telemetry_dir)
    assert ops and ops[-1]["op"] == "detect_changes"
    assert ops[-1]["surface"] == "cli"
    assert ops[-1]["success"] is True
    # Even the empty task is hashed, never stored raw.
    assert "task_hash" in ops[-1]


def test_cli_bundle_markdown_records_op(repo: Path, telemetry_dir: Path) -> None:
    args = argparse.Namespace(
        task="tokens", repo=str(repo), full=False, include_advisory=False, format="markdown"
    )
    protocol_ops.bundle_cmd.run(args)

    ops = _protocol_ops(telemetry_dir)
    assert ops and ops[-1]["op"] == "bundle"
    assert ops[-1]["surface"] == "cli"
    assert ops[-1]["success"] is True
    # output_bytes is measured against the rendered markdown, not JSON.
    assert ops[-1]["output_bytes"] > 0


def test_cli_op_records_failure(
    repo: Path, telemetry_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a, **_kw):
        raise RuntimeError("forced failure")

    import repoctx.protocol as protocol

    monkeypatch.setattr(protocol, "op_scope", boom)
    args = argparse.Namespace(task="tokens", repo=str(repo))
    with pytest.raises(RuntimeError):
        protocol_ops.scope_cmd.run(args)

    ops = _protocol_ops(telemetry_dir)
    assert ops, "a failed op must still be recorded"
    ev = ops[-1]
    assert ev["op"] == "scope"
    assert ev["surface"] == "cli"
    assert ev["success"] is False
    assert ev["error_type"] == "RuntimeError"
    # Off dogfood, no message/traceback is captured (default lane contract).
    assert "error_message" not in ev and "traceback" not in ev


def test_cli_op_failure_captures_dogfood_detail(
    repo: Path, telemetry_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # conftest hard-disables reporting/dogfood suite-wide; opt back into
    # dogfood so capture_exc_detail returns a real message + traceback. The
    # REPOCTX_REPORTING=off kill switch set by conftest keeps is_enabled()
    # False, so nothing is uploaded — the detail only lands in the local log.
    monkeypatch.setenv("REPOCTX_DOGFOOD", "1")

    def boom(*_a, **_kw):
        raise RuntimeError("forced failure")

    import repoctx.protocol as protocol

    monkeypatch.setattr(protocol, "op_scope", boom)
    args = argparse.Namespace(task="tokens", repo=str(repo))
    with pytest.raises(RuntimeError):
        protocol_ops.scope_cmd.run(args)

    ev = _protocol_ops(telemetry_dir)[-1]
    assert ev["success"] is False
    assert ev["error_type"] == "RuntimeError"
    assert ev["error_message"] == "forced failure"
    assert "RuntimeError: forced failure" in ev["traceback"]
