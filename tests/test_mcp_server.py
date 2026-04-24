import json
from pathlib import Path

import pytest

import repoctx.mcp_server as mcp_server
from repoctx.experiment_mcp import arm_control_lane_mcp_suppression
from repoctx.mcp_server import create_server, resolve_repo_root


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture()
def tmp_repo(tmp_path: Path) -> Path:
    """A tmp dir that repoctx's repo-root resolver will accept (has .git)."""
    (tmp_path / ".git").mkdir()
    return tmp_path


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


def test_mcp_server_uses_explicit_repo_root(tmp_repo: Path) -> None:
    write_file(tmp_repo / "AGENTS.md", "# Repo guidance\n")
    write_file(tmp_repo / "src" / "retry.py", "def retry():\n    return True\n")

    server = create_server(repo_root=tmp_repo)
    tool = _get_tool(server, "get_task_context")

    result = tool.fn(task="retry")

    assert any(item["path"] == "AGENTS.md" for item in result["relevant_docs"])


def test_mcp_server_writes_repoctx_telemetry(tmp_repo: Path) -> None:
    tmp_path = tmp_repo
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
    tmp_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path = tmp_repo
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


def _make_git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir()
    return path


def test_resolve_repo_root_prefers_explicit(tmp_path: Path, monkeypatch) -> None:
    repo = _make_git_repo(tmp_path / "explicit")
    other = _make_git_repo(tmp_path / "other")
    monkeypatch.setenv("REPOCTX_REPO_ROOT", str(other))
    monkeypatch.chdir(other)
    assert resolve_repo_root(repo) == repo.resolve()


def test_resolve_repo_root_walks_to_nearest_git_root(tmp_path: Path, monkeypatch) -> None:
    outer = _make_git_repo(tmp_path / "outer")
    inner = _make_git_repo(outer / "nested" / "inner")
    deep = inner / "src" / "pkg"
    deep.mkdir(parents=True)
    monkeypatch.chdir(deep)
    # Starting from deep should land on the *nearest* .git (inner), not outer.
    assert resolve_repo_root(None) == inner.resolve()


def test_resolve_repo_root_accepts_git_file_worktrees(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "worktree"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: /elsewhere/main/.git/worktrees/wt\n")
    monkeypatch.chdir(repo)
    assert resolve_repo_root(None) == repo.resolve()


def test_resolve_repo_root_uses_env_vars(tmp_path: Path, monkeypatch) -> None:
    repo = _make_git_repo(tmp_path / "from_env")
    not_a_repo = tmp_path / "no_git"
    not_a_repo.mkdir()
    monkeypatch.chdir(not_a_repo)
    monkeypatch.setenv("REPOCTX_REPO_ROOT", str(repo))
    assert resolve_repo_root(None) == repo.resolve()


def test_resolve_repo_root_errors_outside_git(tmp_path: Path, monkeypatch) -> None:
    no_git = tmp_path / "blank"
    no_git.mkdir()
    monkeypatch.chdir(no_git)
    for var in ("REPOCTX_REPO_ROOT", "CLAUDE_PROJECT_DIR", "WORKSPACE_FOLDER_PATHS", "VSCODE_CWD"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(RuntimeError) as exc_info:
        resolve_repo_root(None)
    msg = str(exc_info.value)
    assert "--repo" in msg and "REPOCTX_REPO_ROOT" in msg


def test_mcp_server_ignores_telemetry_write_failures(tmp_repo: Path, monkeypatch) -> None:
    tmp_path = tmp_repo
    write_file(tmp_path / "AGENTS.md", "# Repo guidance\n")
    server = create_server(repo_root=tmp_path, telemetry_dir=tmp_path / ".telemetry")
    tool = _get_tool(server, "get_task_context")

    def fail_record(**_: object) -> None:
        raise RuntimeError("telemetry unavailable")

    monkeypatch.setattr(mcp_server, "record_repoctx_invocation", fail_record)

    result = tool.fn(task="retry")

    assert any(item["path"] == "AGENTS.md" for item in result["relevant_docs"])
