from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

SCHEMA_VERSION = 1
REPOCTX_EVENTS_FILE = "repoctx-events.jsonl"
AGENT_RUNS_FILE = "agent-runs.jsonl"
EXPERIMENT_RUNS_FILE = "experiment-runs.jsonl"
ACTIVE_EXPERIMENT_FILE = "active-experiment.json"
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


def _read_jsonl(telemetry_dir: str | Path | None, filename: str) -> list[dict[str, Any]]:
    output_path = get_telemetry_dir(telemetry_dir) / filename
    if not output_path.exists():
        return []
    return [
        json.loads(line)
        for line in output_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def save_active_experiment(
    *,
    telemetry_dir: str | Path | None = None,
    session_id: str,
    repo_root: str | Path,
) -> Path:
    target_dir = get_telemetry_dir(telemetry_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    output_path = target_dir / ACTIVE_EXPERIMENT_FILE
    repo_key = str(Path(repo_root).resolve())
    payload = _load_active_experiment_payload(output_path)
    experiments = payload.setdefault("experiments", {})
    experiments[repo_key] = session_id
    output_path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return output_path


def load_active_experiment(
    *,
    telemetry_dir: str | Path | None = None,
    repo_root: str | Path,
) -> dict[str, str] | None:
    output_path = get_telemetry_dir(telemetry_dir) / ACTIVE_EXPERIMENT_FILE
    if not output_path.exists():
        return None
    payload = _load_active_experiment_payload(output_path)
    if not payload:
        if output_path.exists():
            output_path.unlink()
        return None
    repo_key = str(Path(repo_root).resolve())
    experiments = payload.get("experiments")
    if not isinstance(experiments, dict):
        output_path.unlink(missing_ok=True)
        return None
    session_id = experiments.get(repo_key)
    if not isinstance(session_id, str) or not session_id:
        if repo_key in experiments:
            experiments.pop(repo_key, None)
            if experiments:
                output_path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
            else:
                output_path.unlink(missing_ok=True)
        return None
    return {"session_id": session_id, "repo_root": repo_key}


def clear_active_experiment(
    *,
    telemetry_dir: str | Path | None = None,
    repo_root: str | Path | None = None,
) -> None:
    output_path = get_telemetry_dir(telemetry_dir) / ACTIVE_EXPERIMENT_FILE
    if output_path.exists():
        if repo_root is None:
            output_path.unlink()
            return
        payload = _load_active_experiment_payload(output_path)
        if not payload:
            output_path.unlink()
            return
        experiments = payload.get("experiments")
        if not isinstance(experiments, dict):
            output_path.unlink()
            return
        experiments.pop(str(Path(repo_root).resolve()), None)
        if not experiments:
            output_path.unlink()
            return
        output_path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def _load_active_experiment_payload(output_path: Path) -> dict[str, Any]:
    if not output_path.exists():
        return {"experiments": {}}
    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    if "experiments" in payload:
        experiments = payload.get("experiments")
        if isinstance(experiments, dict):
            return payload
        return {}
    session_id = payload.get("session_id")
    repo_root = payload.get("repo_root")
    if isinstance(session_id, str) and session_id and isinstance(repo_root, str) and repo_root:
        return {"experiments": {repo_root: session_id}}
    return {"experiments": {}}


def _decimal_string(value: Decimal | str | float | int) -> str:
    return str(Decimal(str(value)))


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


def record_protocol_op(
    *,
    telemetry_dir: str | Path | None = None,
    op: str,
    surface: Surface = DEFAULT_SURFACE,
    session_id: str,
    task_id: str,
    task: str,
    repo_root: str | Path,
    success: bool,
    duration_ms: int,
    output_bytes: int,
    error_type: str | None = None,
    extras: dict[str, Any] | None = None,
) -> Path:
    """Record a single repoctx-v2 protocol-op invocation.

    Emits one line per call to ``bundle`` / ``authority`` / ``scope`` /
    ``validate_plan`` / ``risk_report`` / ``refresh``. Used to measure the
    "calls per task" success metric from the v2 design doc.
    """
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "event_type": "protocol_op",
        "event_time": utc_now_seconds(),
        "op": op,
        "surface": surface,
        "session_id": session_id,
        "task_id": task_id,
        "task_hash": sha256_hex(task),
        "repo_hash": sha256_hex(str(Path(repo_root).resolve())),
        "success": success,
        "error_type": error_type,
        "duration_ms": duration_ms,
        "output_bytes": output_bytes,
    }
    if extras:
        payload["extras"] = extras
    written = append_jsonl(telemetry_dir, REPOCTX_EVENTS_FILE, payload)
    # Best-effort upload enqueue. The reporting module is responsible for
    # checking is_enabled() and for stripping path/query-bearing keys before
    # the payload leaves the machine; this call is safe when reporting is off.
    try:
        from repoctx import reporting as _reporting

        _reporting.enqueue_if_enabled(payload, repo_root=repo_root)
    except Exception:  # noqa: BLE001 — never break local telemetry on reporting failure
        pass
    return written


ConsentAction = Literal["prompt_shown", "granted", "declined"]


def record_index_consent_event(
    *,
    telemetry_dir: str | Path | None = None,
    session_id: str,
    surface: Surface = DEFAULT_SURFACE,
    action: ConsentAction,
    repo_root: str | Path,
    previous_action: ConsentAction | None = None,
    duration_ms: int | None = None,
) -> Path:
    """Record one event in the index-consent lifecycle.

    ``action`` is one of ``"prompt_shown"`` (the one-shot consent prompt was
    attached to a retrieval response), ``"granted"`` (the MCP `index` tool ran
    a build to completion), or ``"declined"`` (the MCP `index` tool was called
    with ``decline=true``). The trio captures the full state machine; a stream
    of these is enough to reconstruct prompt → answer conversion rates without
    any path/query/code data ever being recorded.

    ``previous_action`` lets us distinguish "answered for the first time" from
    "changed their mind" (e.g. a previously-declined repo gets granted later);
    it is the recorded ``index_consent`` value *before* this event flipped it,
    or ``None`` when none was recorded yet. ``duration_ms`` is meaningful only
    for ``"granted"`` — the wall-clock time the build took.

    Like ``record_protocol_op``, this enqueues to the reporting layer when
    enabled. The reporting payload-builder already strips path-bearing keys
    and replaces ``repo_hash`` with an install-scoped fingerprint, so the
    only consent-specific knowledge upload needs is the new event shape.
    """
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "event_type": "index_consent",
        "event_time": utc_now_seconds(),
        "session_id": session_id,
        "surface": surface,
        "action": action,
        "repo_hash": sha256_hex(str(Path(repo_root).resolve())),
        "previous_action": previous_action,
        "duration_ms": duration_ms,
    }
    written = append_jsonl(telemetry_dir, REPOCTX_EVENTS_FILE, payload)
    try:
        from repoctx import reporting as _reporting

        _reporting.enqueue_if_enabled(payload, repo_root=repo_root)
    except Exception:  # noqa: BLE001 — never break local telemetry on reporting failure
        pass
    return written


def record_index_build(
    *,
    telemetry_dir: str | Path | None = None,
    session_id: str,
    surface: Surface = DEFAULT_SURFACE,
    repo_root: str | Path,
    success: bool,
    duration_ms: int,
    source: str,
    incremental: bool,
    chunk_count: int,
    file_count: int,
    embedded_chunk_count: int,
    model_load_ms: int | None = None,
    embed_ms: int | None = None,
    scan_ms: int | None = None,
    device: str | None = None,
    dtype: str | None = None,
    model_name: str | None = None,
    output_bytes: int = 0,
    error_type: str | None = None,
) -> Path:
    """Record a single embedding-index build with a timing breakdown.

    Emitted once per ``repoctx index`` / ``rebuild`` (CLI) or ``index`` MCP
    build. The whole point is to *measure* the cost we keep guessing at — most
    importantly the split between ``model_load_ms`` (loading/downloading the
    embedding model, a one-time/cacheable cost) and ``embed_ms`` (encoding the
    corpus, the part that scales with repo size). ``duration_ms`` is the total
    wall-clock so the event lands in ``repoctx stats`` per-op latency
    automatically; the component timings ride alongside for the breakdown.

    Like ``record_protocol_op``, this enqueues to the reporting layer when
    enabled. Every field is a count, timing, or low-cardinality enum — the
    upload boundary already strips path/query/code-bearing keys, and none of
    these are path-bearing (``model_name`` is a constant model id, ``device`` is
    ``cpu``/``cuda``/``mps``), so the breakdown survives redaction intact.
    """
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "event_type": "index_build",
        "event_time": utc_now_seconds(),
        "session_id": session_id,
        "surface": surface,
        "repo_hash": sha256_hex(str(Path(repo_root).resolve())),
        "success": success,
        "error_type": error_type,
        "duration_ms": duration_ms,
        "model_load_ms": model_load_ms,
        "embed_ms": embed_ms,
        "scan_ms": scan_ms,
        "source": source,
        "incremental": incremental,
        "chunk_count": chunk_count,
        "file_count": file_count,
        "embedded_chunk_count": embedded_chunk_count,
        "device": device,
        "dtype": dtype,
        "model_name": model_name,
        "output_bytes": output_bytes,
    }
    written = append_jsonl(telemetry_dir, REPOCTX_EVENTS_FILE, payload)
    try:
        from repoctx import reporting as _reporting

        _reporting.enqueue_if_enabled(payload, repo_root=repo_root)
    except Exception:  # noqa: BLE001 — never break local telemetry on reporting failure
        pass
    return written


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


def record_experiment_session(
    *,
    telemetry_dir: str | Path | None = None,
    session_id: str,
    task_id: str,
    query: str,
    repo_root: str | Path,
    prompt: str,
    base_commit: str,
    control_worktree: str | Path,
    repoctx_worktree: str | Path,
    label: str | None = None,
    guardrail_mode: str | None = None,
) -> Path:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "event_type": "experiment_session",
        "event_time": utc_now_seconds(),
        "session_id": session_id,
        "task_id": task_id,
        "query_hash": sha256_hex(query),
        "repo_hash": sha256_hex(str(Path(repo_root).resolve())),
        "prompt": prompt,
        "prompt_hash": sha256_hex(prompt),
        "base_commit": base_commit,
        "control_worktree": str(Path(control_worktree)),
        "repoctx_worktree": str(Path(repoctx_worktree)),
        "label": label,
        "guardrail_mode": guardrail_mode,
    }
    return append_jsonl(telemetry_dir, EXPERIMENT_RUNS_FILE, payload)


def record_experiment_lane(
    *,
    telemetry_dir: str | Path | None = None,
    session_id: str,
    task_id: str,
    lane: Variant,
    worktree_path: str | Path,
    cost_before_usd: Decimal | str | float | int,
    cost_after_usd: Decimal | str | float | int,
    completion_status: str | None = None,
    verification_status: str | None = None,
    outcome_summary: str | None = None,
    notes: str | None = None,
    stats: dict[str, Any] | None = None,
) -> Path:
    before = Decimal(_decimal_string(cost_before_usd))
    after = Decimal(_decimal_string(cost_after_usd))
    payload = {
        "schema_version": SCHEMA_VERSION,
        "event_type": "experiment_lane",
        "event_time": utc_now_seconds(),
        "session_id": session_id,
        "task_id": task_id,
        "lane": lane,
        "worktree_path": str(Path(worktree_path)),
        "cost_before_usd": _decimal_string(before),
        "cost_after_usd": _decimal_string(after),
        "cost_delta_usd": _decimal_string(after - before),
        "completion_status": completion_status,
        "verification_status": verification_status,
        "outcome_summary": outcome_summary,
        "notes": notes,
        "stats": stats or {},
    }
    return append_jsonl(telemetry_dir, EXPERIMENT_RUNS_FILE, payload)


def load_experiment_session(
    *,
    telemetry_dir: str | Path | None = None,
    session_id: str,
) -> dict[str, Any]:
    session: dict[str, Any] | None = None
    lanes: dict[str, dict[str, Any]] = {}
    for payload in _read_jsonl(telemetry_dir, EXPERIMENT_RUNS_FILE):
        if payload.get("session_id") != session_id:
            continue
        if payload.get("event_type") == "experiment_session":
            session = payload
        elif payload.get("event_type") == "experiment_lane":
            lane = payload.get("lane")
            if lane:
                lanes[lane] = payload
    if session is None:
        raise FileNotFoundError(f"Experiment session not found: {session_id}")
    return {
        "session": session,
        "lanes": lanes,
    }
