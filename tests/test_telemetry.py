import json
from pathlib import Path

from repoctx.telemetry import record_agent_run, record_repoctx_invocation


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
