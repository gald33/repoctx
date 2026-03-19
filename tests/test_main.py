import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from repoctx.experiment import create_experiment_worktrees
from repoctx import main as repoctx_main
from repoctx.telemetry import record_experiment_lane, record_experiment_session


def run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def commit_all(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=RepoCtx Tests",
            "-c",
            "user.email=tests@example.com",
            "commit",
            "-m",
            message,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def init_git_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True, text=True)
    write_file(repo / ".gitignore", ".worktrees/\n")
    write_file(repo / "README.md", "# Demo\n")
    write_file(repo / "src" / "app.py", "def run():\n    return 1\n")
    commit_all(repo, "initial commit")


def setup_experiment_session(repo: Path, telemetry_dir: Path, session_id: str = "session-1") -> dict[str, object]:
    session = create_experiment_worktrees(repo, session_id=session_id)
    record_experiment_session(
        telemetry_dir=telemetry_dir,
        session_id=session_id,
        task_id="task-1",
        query="demo task",
        repo_root=repo,
        prompt="demo task",
        base_commit=session["base_commit"],
        control_worktree=session["control_worktree"],
        repoctx_worktree=session["repoctx_worktree"],
    )
    return session


def test_cli_writes_repoctx_telemetry(tmp_path: Path, monkeypatch, capsys) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    telemetry_dir = tmp_path / ".telemetry"

    monkeypatch.setenv("REPOCTX_TELEMETRY_DIR", str(telemetry_dir))
    monkeypatch.setattr(sys, "argv", ["repoctx", "demo task", "--repo", str(tmp_path), "--format", "json"])

    repoctx_main.main()

    stdout = capsys.readouterr().out
    payload = json.loads(stdout)
    assert "metrics" not in payload
    event_path = telemetry_dir / "repoctx-events.jsonl"
    assert event_path.exists()
    telemetry_payload = json.loads(event_path.read_text(encoding="utf-8").strip())
    assert "query" not in telemetry_payload
    assert "repo_root" not in telemetry_payload


def test_experiment_start_creates_session_and_prints_next_steps(tmp_path: Path, monkeypatch, capsys) -> None:
    telemetry_dir = tmp_path / ".telemetry"
    init_git_repo(tmp_path)
    uuids = iter(["session-1", "task-1"])

    monkeypatch.setenv("REPOCTX_TELEMETRY_DIR", str(telemetry_dir))
    monkeypatch.setattr(repoctx_main, "uuid4", lambda: type("FakeUuid", (), {"hex": next(uuids)})())
    monkeypatch.setattr(sys, "argv", ["repoctx", "experiment", "demo task", "--repo", str(tmp_path)])

    repoctx_main.main()

    stdout = capsys.readouterr().out
    assert "Session: session-1" in stdout
    assert "demo task" in stdout
    assert "repoctx experiment lane record --session-id session-1 --lane control" in stdout
    assert "repoctx experiment summarize --session-id session-1" in stdout
    assert (tmp_path / ".worktrees" / "experiment-session-1-control").exists()
    assert (tmp_path / ".worktrees" / "experiment-session-1-repoctx").exists()

    lines = (telemetry_dir / "experiment-runs.jsonl").read_text(encoding="utf-8").strip().splitlines()
    payload = json.loads(lines[0])
    assert payload["event_type"] == "experiment_session"
    assert payload["prompt"] == "demo task"


def test_experiment_lane_record_writes_costs_and_git_stats(tmp_path: Path, monkeypatch, capsys) -> None:
    telemetry_dir = tmp_path / ".telemetry"
    init_git_repo(tmp_path)
    session = setup_experiment_session(tmp_path, telemetry_dir)
    control = session["control_worktree"]

    write_file(control / "src" / "app.py", "def run():\n    return 2\n")
    write_file(control / "tests" / "test_app.py", "from src.app import run\n\ndef test_run():\n    assert run() == 2\n")

    monkeypatch.setenv("REPOCTX_TELEMETRY_DIR", str(telemetry_dir))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "repoctx",
            "experiment",
            "lane",
            "record",
            "--session-id",
            "session-1",
            "--lane",
            "control",
            "--before",
            "12.41",
            "--after",
            "12.89",
            "--completion-status",
            "completed",
            "--verification-status",
            "passed",
        ],
    )

    repoctx_main.main()

    stdout = capsys.readouterr().out
    assert "Recorded control lane" in stdout

    lines = (telemetry_dir / "experiment-runs.jsonl").read_text(encoding="utf-8").strip().splitlines()
    payload = json.loads(lines[-1])
    assert payload["event_type"] == "experiment_lane"
    assert payload["cost_delta_usd"] == "0.48"
    assert payload["stats"]["files_changed"] == 2
    assert payload["verification_status"] == "passed"


def test_experiment_lane_record_prompts_for_missing_costs(tmp_path: Path, monkeypatch, capsys) -> None:
    telemetry_dir = tmp_path / ".telemetry"
    init_git_repo(tmp_path)
    session = setup_experiment_session(tmp_path, telemetry_dir, session_id="session-2")
    repoctx_worktree = session["repoctx_worktree"]

    write_file(repoctx_worktree / "src" / "app.py", "def run():\n    return 3\n")
    monkeypatch.setenv("REPOCTX_TELEMETRY_DIR", str(telemetry_dir))
    answers = iter(["1.50", "1.75"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "repoctx",
            "experiment",
            "lane",
            "record",
            "--session-id",
            "session-2",
            "--lane",
            "repoctx",
        ],
    )

    repoctx_main.main()

    stdout = capsys.readouterr().out
    assert "Recorded repoctx lane" in stdout
    lines = (telemetry_dir / "experiment-runs.jsonl").read_text(encoding="utf-8").strip().splitlines()
    payload = json.loads(lines[-1])
    assert payload["cost_before_usd"] == "1.50"
    assert payload["cost_after_usd"] == "1.75"


def test_experiment_summarize_prints_controlled_comparison(tmp_path: Path, monkeypatch, capsys) -> None:
    telemetry_dir = tmp_path / ".telemetry"
    init_git_repo(tmp_path)
    session = setup_experiment_session(tmp_path, telemetry_dir, session_id="session-3")

    record_experiment_lane(
        telemetry_dir=telemetry_dir,
        session_id="session-3",
        task_id="task-1",
        lane="control",
        worktree_path=session["control_worktree"],
        cost_before_usd=Decimal("10.00"),
        cost_after_usd=Decimal("10.80"),
        completion_status="completed",
        verification_status="passed",
        stats={"files_changed": 3, "lines_added": 12, "lines_deleted": 2, "net_lines": 10, "new_files": 1, "modified_files": 2, "source_files_changed": 1, "test_files_changed": 1, "docs_files_changed": 1, "config_files_changed": 0},
    )
    record_experiment_lane(
        telemetry_dir=telemetry_dir,
        session_id="session-3",
        task_id="task-1",
        lane="repoctx",
        worktree_path=session["repoctx_worktree"],
        cost_before_usd=Decimal("10.80"),
        cost_after_usd=Decimal("11.10"),
        completion_status="completed",
        verification_status="passed",
        stats={"files_changed": 2, "lines_added": 8, "lines_deleted": 1, "net_lines": 7, "new_files": 0, "modified_files": 2, "source_files_changed": 1, "test_files_changed": 1, "docs_files_changed": 0, "config_files_changed": 0},
    )
    monkeypatch.setenv("REPOCTX_TELEMETRY_DIR", str(telemetry_dir))
    monkeypatch.setattr(sys, "argv", ["repoctx", "experiment", "summarize", "--session-id", "session-3"])

    repoctx_main.main()

    stdout = capsys.readouterr().out
    assert "Experiment summary" in stdout
    assert "repoctx saved: $0.50" in stdout
    assert "prompt hash" in stdout.lower()


def test_experiment_summarize_shows_missing_lane(tmp_path: Path, monkeypatch, capsys) -> None:
    telemetry_dir = tmp_path / ".telemetry"
    init_git_repo(tmp_path)
    session = setup_experiment_session(tmp_path, telemetry_dir, session_id="session-4")

    record_experiment_lane(
        telemetry_dir=telemetry_dir,
        session_id="session-4",
        task_id="task-1",
        lane="control",
        worktree_path=session["control_worktree"],
        cost_before_usd=Decimal("3.00"),
        cost_after_usd=Decimal("3.40"),
        stats={"files_changed": 1, "lines_added": 2, "lines_deleted": 0, "net_lines": 2, "new_files": 1, "modified_files": 0, "source_files_changed": 1, "test_files_changed": 0, "docs_files_changed": 0, "config_files_changed": 0},
    )
    monkeypatch.setenv("REPOCTX_TELEMETRY_DIR", str(telemetry_dir))
    monkeypatch.setattr(sys, "argv", ["repoctx", "experiment", "summarize", "--session-id", "session-4"])

    repoctx_main.main()

    stdout = capsys.readouterr().out
    assert "Missing lane results: repoctx" in stdout
    assert "--lane repoctx" in stdout


def test_cli_records_failure_telemetry_and_exits(tmp_path: Path, monkeypatch) -> None:
    telemetry_dir = tmp_path / ".telemetry"
    missing_repo = tmp_path / "missing"

    monkeypatch.setenv("REPOCTX_TELEMETRY_DIR", str(telemetry_dir))
    monkeypatch.setattr(sys, "argv", ["repoctx", "demo task", "--repo", str(missing_repo)])

    with pytest.raises(SystemExit) as exc_info:
        repoctx_main.main()

    assert exc_info.value.code == 1
    event_path = telemetry_dir / "repoctx-events.jsonl"
    assert event_path.exists()
    telemetry_payload = json.loads(event_path.read_text(encoding="utf-8").strip())
    assert "query" not in telemetry_payload
    assert "repo_root" not in telemetry_payload


def test_cli_help_includes_examples_and_subcommand_guidance(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["repoctx", "--help"])

    with pytest.raises(SystemExit) as exc_info:
        repoctx_main.main()

    assert exc_info.value.code == 0
    stdout = capsys.readouterr().out
    assert "usage: repoctx [-h] TASK" in stdout
    assert "Examples:" in stdout
    assert 'repoctx "refactor the auth middleware to support OAuth"' in stdout
    assert "repoctx query \"show me tests related to the billing webhook flow\" --repo /path/to/repo --format json" in stdout
    assert "Use `repoctx query TASK [flags]` when you need query-specific options." in stdout
    assert "repoctx experiment \"refactor the auth middleware to support OAuth\"" in stdout
    assert "Common subcommands:" in stdout
    assert "query" in stdout
    assert "experiment" in stdout
