import json
from decimal import Decimal
from pathlib import Path

from repoctx.telemetry import (
    clear_active_experiment,
    load_active_experiment,
    load_experiment_session,
    record_agent_run,
    record_experiment_lane,
    record_experiment_session,
    record_repoctx_invocation,
    save_active_experiment,
)


def test_record_repoctx_invocation_writes_jsonl(tmp_path: Path) -> None:
    telemetry_dir = tmp_path / "telemetry"

    record_repoctx_invocation(
        telemetry_dir=telemetry_dir,
        session_id="session-1",
        task_id="task-1",
        variant="repoctx",
        surface="cli",
        query="add retry jitter",
        repo_root=tmp_path,
        success=True,
        repoctx_duration_ms=123,
        scan_duration_ms=45,
        files_considered=10,
        files_selected=2,
        docs_selected=1,
        tests_selected=1,
        neighbors_selected=1,
        output_format="markdown",
        output_bytes=512,
    )

    event_path = telemetry_dir / "repoctx-events.jsonl"
    assert event_path.exists()

    payload = json.loads(event_path.read_text(encoding="utf-8").strip())
    assert payload["event_type"] == "repoctx_invocation"
    assert payload["schema_version"] == 1
    assert payload["event_time"].endswith("Z")
    assert "." not in payload["event_time"]
    assert "query" not in payload
    assert "repo_root" not in payload
    assert payload["query_hash"] != "add retry jitter"
    assert payload["repo_hash"] != str(tmp_path)


def test_record_agent_run_writes_jsonl(tmp_path: Path) -> None:
    telemetry_dir = tmp_path / "telemetry"

    record_agent_run(
        telemetry_dir=telemetry_dir,
        session_id="session-1",
        task_id="task-1",
        variant="control",
        surface="cli",
        query="add retry jitter",
        repo_root=tmp_path,
        runner="cursor-agent",
        success=True,
        completion_status="completed",
        agent_duration_ms=456,
        tool_calls=3,
        prompt_tokens=1000,
        completion_tokens=250,
        total_tokens=1250,
        estimated_cost_usd=0.042,
        task_completed=True,
        quality_score=0.9,
    )

    event_path = telemetry_dir / "agent-runs.jsonl"
    assert event_path.exists()
    payload = json.loads(event_path.read_text(encoding="utf-8").strip())

    assert payload["event_type"] == "agent_run"
    assert payload["task_id"] == "task-1"
    assert payload["variant"] == "control"
    assert "query" not in payload
    assert "repo_root" not in payload
    assert isinstance(payload["prompt_tokens"], int)
    assert isinstance(payload["completion_tokens"], int)
    assert isinstance(payload["total_tokens"], int)
    assert isinstance(payload["estimated_cost_usd"], float)


def test_record_experiment_session_writes_jsonl(tmp_path: Path) -> None:
    telemetry_dir = tmp_path / "telemetry"

    record_experiment_session(
        telemetry_dir=telemetry_dir,
        session_id="session-1",
        task_id="task-1",
        query="add retry jitter",
        repo_root=tmp_path,
        prompt="Use the exact same prompt in both lanes.",
        base_commit="abc1234",
        control_worktree=tmp_path / ".worktrees" / "control",
        repoctx_worktree=tmp_path / ".worktrees" / "repoctx",
        label="strict-docs",
        guardrail_mode="strict",
    )

    event_path = telemetry_dir / "experiment-runs.jsonl"
    assert event_path.exists()
    payload = json.loads(event_path.read_text(encoding="utf-8").strip())

    assert payload["event_type"] == "experiment_session"
    assert payload["session_id"] == "session-1"
    assert payload["task_id"] == "task-1"
    assert payload["prompt"] == "Use the exact same prompt in both lanes."
    assert payload["prompt_hash"] != payload["prompt"]
    assert payload["repo_hash"] != str(tmp_path)
    assert payload["control_worktree"].endswith("/control")
    assert payload["repoctx_worktree"].endswith("/repoctx")
    assert payload["label"] == "strict-docs"
    assert payload["guardrail_mode"] == "strict"


def test_record_experiment_lane_writes_jsonl_and_loads_session(tmp_path: Path) -> None:
    telemetry_dir = tmp_path / "telemetry"
    control_path = tmp_path / ".worktrees" / "control"
    repoctx_path = tmp_path / ".worktrees" / "repoctx"

    record_experiment_session(
        telemetry_dir=telemetry_dir,
        session_id="session-1",
        task_id="task-1",
        query="add retry jitter",
        repo_root=tmp_path,
        prompt="Use the exact same prompt in both lanes.",
        base_commit="abc1234",
        control_worktree=control_path,
        repoctx_worktree=repoctx_path,
    )
    record_experiment_lane(
        telemetry_dir=telemetry_dir,
        session_id="session-1",
        task_id="task-1",
        lane="control",
        worktree_path=control_path,
        cost_before_usd=Decimal("12.41"),
        cost_after_usd=Decimal("12.89"),
        completion_status="completed",
        verification_status="passed",
        outcome_summary="Implemented the feature.",
        notes="No issues.",
        stats={
            "files_changed": 2,
            "lines_added": 18,
            "lines_deleted": 4,
            "net_lines": 14,
            "new_files": 1,
            "modified_files": 1,
            "source_files_changed": 1,
            "test_files_changed": 1,
            "docs_files_changed": 0,
            "config_files_changed": 0,
        },
    )

    lines = (telemetry_dir / "experiment-runs.jsonl").read_text(encoding="utf-8").strip().splitlines()
    payload = json.loads(lines[-1])

    assert payload["event_type"] == "experiment_lane"
    assert payload["lane"] == "control"
    assert payload["cost_before_usd"] == "12.41"
    assert payload["cost_after_usd"] == "12.89"
    assert payload["cost_delta_usd"] == "0.48"
    assert payload["stats"]["files_changed"] == 2

    session = load_experiment_session(telemetry_dir=telemetry_dir, session_id="session-1")

    assert session["session"]["prompt_hash"] == session["session"]["prompt_hash"]
    assert session["lanes"]["control"]["cost_delta_usd"] == "0.48"
    assert session["lanes"]["control"]["verification_status"] == "passed"
    assert "repoctx" not in session["lanes"]


def test_active_experiment_state_round_trip(tmp_path: Path) -> None:
    telemetry_dir = tmp_path / "telemetry"
    repo_root = tmp_path / "repo-a"

    assert load_active_experiment(telemetry_dir=telemetry_dir, repo_root=repo_root) is None

    state_path = save_active_experiment(
        telemetry_dir=telemetry_dir,
        session_id="session-9",
        repo_root=repo_root,
    )
    assert state_path.exists()
    assert load_active_experiment(telemetry_dir=telemetry_dir, repo_root=repo_root) == {
        "session_id": "session-9",
        "repo_root": str(repo_root.resolve()),
    }

    clear_active_experiment(telemetry_dir=telemetry_dir, repo_root=repo_root)
    assert load_active_experiment(telemetry_dir=telemetry_dir, repo_root=repo_root) is None


def test_active_experiment_state_tracks_multiple_repos(tmp_path: Path) -> None:
    telemetry_dir = tmp_path / "telemetry"
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"

    save_active_experiment(telemetry_dir=telemetry_dir, session_id="session-a", repo_root=repo_a)
    save_active_experiment(telemetry_dir=telemetry_dir, session_id="session-b", repo_root=repo_b)

    assert load_active_experiment(telemetry_dir=telemetry_dir, repo_root=repo_a) == {
        "session_id": "session-a",
        "repo_root": str(repo_a.resolve()),
    }
    assert load_active_experiment(telemetry_dir=telemetry_dir, repo_root=repo_b) == {
        "session_id": "session-b",
        "repo_root": str(repo_b.resolve()),
    }


def test_load_active_experiment_ignores_invalid_json(tmp_path: Path) -> None:
    telemetry_dir = tmp_path / "telemetry"
    telemetry_dir.mkdir(parents=True, exist_ok=True)
    state_path = telemetry_dir / "active-experiment.json"
    state_path.write_text("{not-json}\n", encoding="utf-8")

    assert load_active_experiment(telemetry_dir=telemetry_dir, repo_root=tmp_path / "repo-a") is None
    assert not state_path.exists()


def test_load_active_experiment_ignores_non_object_json(tmp_path: Path) -> None:
    telemetry_dir = tmp_path / "telemetry"
    telemetry_dir.mkdir(parents=True, exist_ok=True)
    state_path = telemetry_dir / "active-experiment.json"
    state_path.write_text("[]\n", encoding="utf-8")

    assert load_active_experiment(telemetry_dir=telemetry_dir, repo_root=tmp_path / "repo-a") is None
    assert not state_path.exists()
