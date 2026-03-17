# RepoCtx Telemetry Design

**Date:** 2026-03-17

**Goal:** Add privacy-first local telemetry that makes it possible to measure whether using `repoctx` reduces total agent token usage, cost, and time versus a no-`repoctx` control run.

## Scope

The telemetry system should answer one question clearly: does `repoctx` create enough downstream agent efficiency to justify its own overhead?

The first version should stay intentionally narrow:

- log telemetry locally only
- use append-only JSONL files
- support both CLI and MCP usage
- default to hashed identifiers instead of raw task text or repo paths
- capture enough data to compare paired `control` and `repoctx` runs for the same task

This version should not add remote export, dashboards, prompt capture, or detailed per-file analytics.

## Chosen Approach

Add a small telemetry module that writes structured JSONL events under the user's home directory. The module should support two linked event types:

- `repoctx_invocation` for `repoctx` runtime and output metrics
- `agent_run` for downstream agent cost and result metrics

The telemetry design should link both event types with a shared `task_id`, `session_id`, and `variant` field so paired experiments are easy to compare:

- `variant=control` means the agent ran without `repoctx`
- `variant=repoctx` means the agent used `repoctx`

The initial implementation should automatically emit `repoctx_invocation` events from both the CLI and MCP surfaces. It should also expose a lightweight public API and CLI flags for recording `agent_run` events from external benchmarks or wrappers.

This keeps the core product simple while still enabling serious measurement.

## Timestamping And Measurement

Store two different kinds of time data:

- `event_time` as an ISO 8601 UTC timestamp with second resolution
- durations as integer milliseconds measured with a monotonic clock

Example:

- `event_time: 2026-03-17T15:04:12Z`
- `repoctx_duration_ms: 842`

Second-resolution wall-clock timestamps are enough for human-readable sequencing and local debugging. Millisecond durations carry the useful performance detail without introducing false precision.

## Event Schema

All event types should include:

- `schema_version`
- `event_type`
- `event_time`
- `session_id`
- `task_id`
- `variant`
- `surface`

### `repoctx_invocation`

Fields:

- `query_hash`
- `repo_hash`
- `success`
- `error_type`
- `repoctx_duration_ms`
- `scan_duration_ms`
- `files_considered`
- `files_selected`
- `docs_selected`
- `tests_selected`
- `neighbors_selected`
- `output_format`
- `output_bytes`

### `agent_run`

Fields:

- `query_hash`
- `repo_hash`
- `runner`
- `success`
- `completion_status`
- `agent_duration_ms`
- `tool_calls`
- `prompt_tokens`
- `completion_tokens`
- `total_tokens`
- `estimated_cost_usd`
- `task_completed`
- `quality_score`

`quality_score` should be optional because some experiments may only have binary completion results.

## Storage Layout

Write local telemetry beneath a dedicated directory:

- `~/.repoctx/telemetry/repoctx-events.jsonl`
- `~/.repoctx/telemetry/agent-runs.jsonl`

Each line should be a complete JSON object. The telemetry writer should create parent directories on demand and tolerate the telemetry directory not existing yet.

If telemetry writing fails, `repoctx` should continue to work and log a warning only when verbose logging is enabled. Telemetry must never become a hard runtime dependency.

## Privacy Defaults

The default mode should log only privacy-preserving data:

- hash task text before storing it
- hash the resolved repo root before storing it
- avoid raw prompts, raw selected file paths, and file contents
- keep telemetry local by default

Add an explicit debug mode later if richer local capture becomes useful, but keep it opt-in and clearly documented.

## Files Expected To Change

- `repoctx/main.py`
- `repoctx/mcp_server.py`
- `repoctx/retriever.py`
- `repoctx/models.py`
- `repoctx/__init__.py`
- `tests/test_mcp_server.py`
- `tests/test_retriever.py`
- `README.md`

New files:

- `repoctx/telemetry.py`
- `tests/test_telemetry.py`
- `tests/test_main.py`

## Rollout Phases

### Phase 1

Implement local `repoctx_invocation` events for CLI and MCP usage.

### Phase 2

Add a public API and CLI entry point for recording linked `agent_run` events from external experiment harnesses.

### Phase 3

Add a small local analysis utility that summarizes paired `control` versus `repoctx` runs by token, cost, latency, and success deltas.

## Verification

- Add unit tests for the telemetry writer, hashing, and timestamp formatting.
- Add a CLI test that verifies a `repoctx_invocation` event is written for a successful run.
- Extend MCP tests to verify telemetry is emitted for server tool usage.
- Add a retriever-level test that exercises the metrics collected for selection counts and output size.
- Run the full `pytest` suite after the targeted tests pass.
