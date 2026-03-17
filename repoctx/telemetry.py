from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

SCHEMA_VERSION = 1
REPOCTX_EVENTS_FILE = "repoctx-events.jsonl"
AGENT_RUNS_FILE = "agent-runs.jsonl"
DEFAULT_VARIANT = "repoctx"
DEFAULT_SURFACE = "cli"

Variant = Literal["control", "repoctx"]
Surface = Literal["cli", "mcp"]


def get_telemetry_dir(telemetry_dir: str | Path | None = None) -> Path:
    if telemetry_dir is not None:
        return Path(telemetry_dir)
    override = os.environ.get("REPOCTX_TELEMETRY_DIR")
    if override:
        return Path(override)
    return Path.home() / ".repoctx" / "telemetry"


def utc_now_seconds() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def append_jsonl(telemetry_dir: str | Path | None, filename: str, payload: dict[str, Any]) -> Path:
    target_dir = get_telemetry_dir(telemetry_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    output_path = target_dir / filename
    serialized = json.dumps(payload, sort_keys=True) + "\n"
    with output_path.open("a", encoding="utf-8") as handle:
        handle.write(serialized)
    return output_path


def record_repoctx_invocation(
    *,
    telemetry_dir: str | Path | None = None,
    session_id: str,
    task_id: str,
    variant: Variant = DEFAULT_VARIANT,
    surface: Surface = DEFAULT_SURFACE,
    query: str,
    repo_root: str | Path,
    success: bool,
    repoctx_duration_ms: int,
    scan_duration_ms: int,
    files_considered: int,
    files_selected: int,
    docs_selected: int,
    tests_selected: int,
    neighbors_selected: int,
    output_format: str,
    output_bytes: int,
    error_type: str | None = None,
) -> Path:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "event_type": "repoctx_invocation",
        "event_time": utc_now_seconds(),
        "session_id": session_id,
        "task_id": task_id,
        "variant": variant,
        "surface": surface,
        "query_hash": sha256_hex(query),
        "repo_hash": sha256_hex(str(Path(repo_root).resolve())),
        "success": success,
        "error_type": error_type,
        "repoctx_duration_ms": repoctx_duration_ms,
        "scan_duration_ms": scan_duration_ms,
        "files_considered": files_considered,
        "files_selected": files_selected,
        "docs_selected": docs_selected,
        "tests_selected": tests_selected,
        "neighbors_selected": neighbors_selected,
        "output_format": output_format,
        "output_bytes": output_bytes,
    }
    return append_jsonl(telemetry_dir, REPOCTX_EVENTS_FILE, payload)


def record_agent_run(
    *,
    telemetry_dir: str | Path | None = None,
    session_id: str,
    task_id: str,
    variant: Variant,
    surface: Surface = DEFAULT_SURFACE,
    query: str,
    repo_root: str | Path,
    runner: str,
    success: bool,
    completion_status: str,
    agent_duration_ms: int,
    tool_calls: int,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    estimated_cost_usd: float,
    task_completed: bool | None = None,
    quality_score: float | None = None,
) -> Path:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "event_type": "agent_run",
        "event_time": utc_now_seconds(),
        "session_id": session_id,
        "task_id": task_id,
        "variant": variant,
        "surface": surface,
        "query_hash": sha256_hex(query),
        "repo_hash": sha256_hex(str(Path(repo_root).resolve())),
        "runner": runner,
        "success": success,
        "completion_status": completion_status,
        "agent_duration_ms": agent_duration_ms,
        "tool_calls": tool_calls,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "estimated_cost_usd": estimated_cost_usd,
        "task_completed": task_completed,
        "quality_score": quality_score,
    }
    return append_jsonl(telemetry_dir, AGENT_RUNS_FILE, payload)
