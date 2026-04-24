"""Tests for per-op telemetry emission on the v2 MCP tools."""

from __future__ import annotations

import json
from pathlib import Path

from repoctx.mcp_server import create_server


def _get_tool(server, name: str):
    for tool in server._tool_manager.list_tools():
        if tool.name == name:
            return tool
    raise AssertionError(f"tool {name!r} not registered")


def test_protocol_op_event_is_written(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "AGENTS.md").write_text("# Agents\n")
    telemetry_dir = tmp_path / ".telemetry"

    server = create_server(repo_root=tmp_path, telemetry_dir=telemetry_dir)
    tool = _get_tool(server, "scope")
    tool.fn(task="anything")

    events_path = telemetry_dir / "repoctx-events.jsonl"
    lines = events_path.read_text(encoding="utf-8").splitlines()
    ops = [json.loads(l) for l in lines if json.loads(l).get("event_type") == "protocol_op"]
    assert ops, "expected a protocol_op event"
    ev = ops[-1]
    assert ev["op"] == "scope"
    assert ev["surface"] == "mcp"
    assert ev["success"] is True
    assert ev["duration_ms"] >= 0
    assert ev["output_bytes"] > 0
    assert "task_hash" in ev and "repo_hash" in ev
    assert "task" not in ev, "raw task must not be persisted"


def test_protocol_op_event_records_failure(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / ".git").mkdir()
    telemetry_dir = tmp_path / ".telemetry"
    server = create_server(repo_root=tmp_path, telemetry_dir=telemetry_dir)

    def boom(*_a, **_kw):
        raise RuntimeError("forced failure")

    import repoctx.protocol.bundle_op as bundle_op

    monkeypatch.setattr(bundle_op, "build_bundle", boom)
    tool = _get_tool(server, "bundle")
    try:
        tool.fn(task="anything")
    except RuntimeError:
        pass

    lines = (telemetry_dir / "repoctx-events.jsonl").read_text().splitlines()
    ops = [json.loads(l) for l in lines if json.loads(l).get("event_type") == "protocol_op"]
    assert ops and ops[-1]["success"] is False
    assert ops[-1]["error_type"] == "RuntimeError"
