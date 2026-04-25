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


def _isolate_resolution(monkeypatch, cache_dir: Path) -> None:
    """Strip env signals and isolate the cache so resolver tests are hermetic."""
    for var in (
        "REPOCTX_REPO_ROOT",
        "CLAUDE_PROJECT_DIR",
        "WORKSPACE_FOLDER_PATHS",
        "VSCODE_CWD",
        "PWD",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("REPOCTX_CACHE_DIR", str(cache_dir))


def test_resolve_repo_root_uses_pwd_when_cwd_is_root(tmp_path: Path, monkeypatch) -> None:
    repo = _make_git_repo(tmp_path / "from_pwd")
    cache_dir = tmp_path / "cache"
    _isolate_resolution(monkeypatch, cache_dir)
    monkeypatch.chdir("/")
    monkeypatch.setenv("PWD", str(repo))
    assert resolve_repo_root(None) == repo.resolve()


def test_resolve_repo_root_does_not_auto_pick_from_recency_log(
    tmp_path: Path, monkeypatch
) -> None:
    """Multi-repo safety: a populated recency log must NOT silently resolve."""
    import json as _json

    repo = _make_git_repo(tmp_path / "recent")
    cache_dir = tmp_path / "cache"
    _isolate_resolution(monkeypatch, cache_dir)
    cache_dir.mkdir(parents=True)
    (cache_dir / "recent_repos.json").write_text(
        _json.dumps([{"path": str(repo), "last_used": 1.0}]), encoding="utf-8"
    )
    monkeypatch.chdir("/")
    with pytest.raises(RuntimeError) as exc_info:
        resolve_repo_root(None)
    # Error must surface the recent repo so the model knows what to pass.
    assert str(repo) in str(exc_info.value)


def test_resolve_repo_root_persists_recency_on_success(tmp_path: Path, monkeypatch) -> None:
    import json as _json

    repo = _make_git_repo(tmp_path / "fresh")
    cache_dir = tmp_path / "cache"
    _isolate_resolution(monkeypatch, cache_dir)
    monkeypatch.chdir(repo)
    resolve_repo_root(None)
    log = _json.loads((cache_dir / "recent_repos.json").read_text(encoding="utf-8"))
    assert log[0]["path"] == str(repo.resolve())


def test_resolve_repo_root_recency_log_dedupes_and_orders(
    tmp_path: Path, monkeypatch
) -> None:
    import json as _json

    a = _make_git_repo(tmp_path / "a")
    b = _make_git_repo(tmp_path / "b")
    cache_dir = tmp_path / "cache"
    _isolate_resolution(monkeypatch, cache_dir)
    monkeypatch.chdir(a)
    resolve_repo_root(None)
    monkeypatch.chdir(b)
    resolve_repo_root(None)
    monkeypatch.chdir(a)
    resolve_repo_root(None)  # bumps `a` back to the front
    log = _json.loads((cache_dir / "recent_repos.json").read_text(encoding="utf-8"))
    paths = [e["path"] for e in log]
    assert paths == [str(a.resolve()), str(b.resolve())]


def test_mcp_server_memoizes_resolved_root_within_process(
    tmp_path: Path, monkeypatch
) -> None:
    """After one successful resolution, subsequent calls reuse it without re-walking."""
    repo = _make_git_repo(tmp_path / "memoized")
    write_file(repo / "AGENTS.md", "# Repo guidance\n")
    cache_dir = tmp_path / "cache"
    _isolate_resolution(monkeypatch, cache_dir)
    monkeypatch.chdir("/")  # no live signals — only per-call arg can resolve
    server = create_server()
    tool = _get_tool(server, "get_task_context")
    # First call must pass repo_root explicitly.
    tool.fn(task="retry", repo_root=str(repo))
    # Second call omits repo_root; should still succeed via session memo,
    # despite cwd=/ and no env signals.
    result = tool.fn(task="retry")
    assert any(item["path"] == "AGENTS.md" for item in result["relevant_docs"])


def test_mcp_server_explicit_repo_root_switches_session_memo(
    tmp_path: Path, monkeypatch
) -> None:
    """A per-call repo_root replaces the session memo (model can switch repos)."""
    repo_a = _make_git_repo(tmp_path / "a")
    repo_b = _make_git_repo(tmp_path / "b")
    write_file(repo_a / "ALPHA.md", "# Repo A\n")
    write_file(repo_b / "BETA.md", "# Repo B\n")
    cache_dir = tmp_path / "cache"
    _isolate_resolution(monkeypatch, cache_dir)
    monkeypatch.chdir("/")
    server = create_server()
    tool = _get_tool(server, "get_task_context")
    # Resolve to A.
    res_a = tool.fn(task="alpha", repo_root=str(repo_a))
    assert any(d["path"] == "ALPHA.md" for d in res_a["relevant_docs"])
    # Switch to B mid-session — explicit override must replace the memo.
    res_b = tool.fn(task="beta", repo_root=str(repo_b))
    assert any(d["path"] == "BETA.md" for d in res_b["relevant_docs"])
    # Now an unspecified call should target B (the new memo), not A.
    res_b2 = tool.fn(task="beta")
    assert any(d["path"] == "BETA.md" for d in res_b2["relevant_docs"])


def test_mcp_tool_accepts_per_call_repo_root(tmp_path: Path, monkeypatch) -> None:
    """A tool's repo_root arg overrides server-startup state and env signals."""
    repo = _make_git_repo(tmp_path / "per_call")
    write_file(repo / "AGENTS.md", "# Repo guidance\n")
    cache_dir = tmp_path / "cache"
    _isolate_resolution(monkeypatch, cache_dir)
    monkeypatch.chdir("/")  # no other signals — only the per-call arg can resolve
    server = create_server()  # startup resolution fails; server still boots
    tool = _get_tool(server, "get_task_context")
    result = tool.fn(task="retry", repo_root=str(repo))
    assert any(item["path"] == "AGENTS.md" for item in result["relevant_docs"])


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
