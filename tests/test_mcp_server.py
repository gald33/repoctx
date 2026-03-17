import json
from pathlib import Path

import repoctx.mcp_server as mcp_server
from repoctx.mcp_server import create_server


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_mcp_server_registers_get_task_context_tool() -> None:
    server = create_server()

    tools = server._tool_manager.list_tools()

    assert len(tools) == 1
    assert tools[0].name == "get_task_context"
    assert tools[0].parameters["required"] == ["task"]


def test_mcp_server_uses_explicit_repo_root(tmp_path: Path) -> None:
    write_file(tmp_path / "AGENTS.md", "# Repo guidance\n")
    write_file(tmp_path / "src" / "retry.py", "def retry():\n    return True\n")

    server = create_server(repo_root=tmp_path)
    tool = server._tool_manager.list_tools()[0]

    result = tool.fn(task="retry")

    assert any(item["path"] == "AGENTS.md" for item in result["relevant_docs"])


def test_mcp_server_writes_repoctx_telemetry(tmp_path: Path) -> None:
    write_file(tmp_path / "AGENTS.md", "# Repo guidance\n")
    telemetry_dir = tmp_path / ".telemetry"

    server = create_server(repo_root=tmp_path, telemetry_dir=telemetry_dir)
    tool = server._tool_manager.list_tools()[0]

    tool.fn(task="retry")

    event_path = telemetry_dir / "repoctx-events.jsonl"
    lines = event_path.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["event_type"] == "repoctx_invocation"
    assert payload["surface"] == "mcp"
    assert "query" not in payload
    assert "repo_root" not in payload


def test_mcp_server_ignores_telemetry_write_failures(tmp_path: Path, monkeypatch) -> None:
    write_file(tmp_path / "AGENTS.md", "# Repo guidance\n")
    server = create_server(repo_root=tmp_path, telemetry_dir=tmp_path / ".telemetry")
    tool = server._tool_manager.list_tools()[0]

    def fail_record(**_: object) -> None:
        raise RuntimeError("telemetry unavailable")

    monkeypatch.setattr(mcp_server, "record_repoctx_invocation", fail_record)

    result = tool.fn(task="retry")

    assert any(item["path"] == "AGENTS.md" for item in result["relevant_docs"])
