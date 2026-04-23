import json
from pathlib import Path

import pytest

import repoctx.mcp_server as mcp_server
from repoctx.experiment_mcp import arm_control_lane_mcp_suppression
from repoctx.mcp_server import create_server


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _get_tool(server, name: str):
    for tool in server._tool_manager.list_tools():
        if tool.name == name:
            return tool
    raise AssertionError(f"tool {name!r} not registered")


def test_mcp_server_registers_get_task_context_tool() -> None:
    server = create_server()

    names = {t.name for t in server._tool_manager.list_tools()}

    assert "get_task_context" in names
    # repoctx v2 protocol ops must also be registered alongside the legacy tool.
    assert {"bundle", "authority", "scope", "validate_plan", "risk_report", "refresh"}.issubset(names)
    get_tc = _get_tool(server, "get_task_context")
    assert get_tc.parameters["required"] == ["task"]


def test_mcp_server_uses_explicit_repo_root(tmp_path: Path) -> None:
    write_file(tmp_path / "AGENTS.md", "# Repo guidance\n")
    write_file(tmp_path / "src" / "retry.py", "def retry():\n    return True\n")

    server = create_server(repo_root=tmp_path)
    tool = _get_tool(server, "get_task_context")

    result = tool.fn(task="retry")

    assert any(item["path"] == "AGENTS.md" for item in result["relevant_docs"])


def test_mcp_server_writes_repoctx_telemetry(tmp_path: Path) -> None:
    write_file(tmp_path / "AGENTS.md", "# Repo guidance\n")
    telemetry_dir = tmp_path / ".telemetry"

    server = create_server(repo_root=tmp_path, telemetry_dir=telemetry_dir)
    tool = _get_tool(server, "get_task_context")

    tool.fn(task="retry")

    event_path = telemetry_dir / "repoctx-events.jsonl"
    lines = event_path.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["event_type"] == "repoctx_invocation"
    assert payload["surface"] == "mcp"
    assert "query" not in payload
    assert "repo_root" not in payload


def test_mcp_server_returns_stub_when_experiment_mcp_suppressed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_file(tmp_path / "AGENTS.md", "# Repo guidance\n")
    telemetry_dir = tmp_path / ".telemetry"
    cfg = tmp_path / "repoctx-config.json"
    cfg.write_text(
        json.dumps(
            {
                "experiment_mcp_suppress": True,
                "experiment_mcp_idle_ttl_seconds": 3600,
                "experiment_mcp_extend_seconds": 600,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("REPOCTX_CONFIG_PATH", str(cfg))
    monkeypatch.setattr("repoctx.experiment_mcp.time.time", lambda: 10_000.0)
    assert arm_control_lane_mcp_suppression(telemetry_dir=telemetry_dir) is True
    monkeypatch.setattr("repoctx.experiment_mcp.time.time", lambda: 10_100.0)

    server = create_server(repo_root=tmp_path, telemetry_dir=telemetry_dir)
    tool = _get_tool(server, "get_task_context")
    result = tool.fn(task="retry")

    assert result.get("experiment_mcp_suppressed") is True
    assert result["relevant_docs"] == []
    assert "control-lane experiment" in result["context_markdown"]
    event_path = telemetry_dir / "repoctx-events.jsonl"
    payload = json.loads(event_path.read_text(encoding="utf-8").strip())
    assert payload["success"] is False
    assert payload["error_type"] == "ExperimentMcpSuppressed"


def test_mcp_server_ignores_telemetry_write_failures(tmp_path: Path, monkeypatch) -> None:
    write_file(tmp_path / "AGENTS.md", "# Repo guidance\n")
    server = create_server(repo_root=tmp_path, telemetry_dir=tmp_path / ".telemetry")
    tool = _get_tool(server, "get_task_context")

    def fail_record(**_: object) -> None:
        raise RuntimeError("telemetry unavailable")

    monkeypatch.setattr(mcp_server, "record_repoctx_invocation", fail_record)

    result = tool.fn(task="retry")

    assert any(item["path"] == "AGENTS.md" for item in result["relevant_docs"])
