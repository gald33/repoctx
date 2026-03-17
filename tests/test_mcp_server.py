from pathlib import Path

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
