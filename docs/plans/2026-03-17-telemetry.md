# RepoCtx Telemetry Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add privacy-first local telemetry that records `repoctx` invocation metrics and supports paired `control` versus `repoctx` agent-cost experiments.

**Architecture:** Introduce a dedicated telemetry module that writes append-only JSONL events under `~/.repoctx/telemetry`. Thread lightweight runtime metrics from the retriever through the CLI and MCP entry points so `repoctx_invocation` events are emitted automatically, then expose a small public recording API for downstream `agent_run` events.

**Tech Stack:** Python 3.11+, dataclasses, pathlib, hashlib, json, time, pytest

---

### Task 1: Add telemetry data models and JSONL writer

**Files:**
- Create: `repoctx/telemetry.py`
- Modify: `repoctx/__init__.py`
- Test: `tests/test_telemetry.py`

**Step 1: Write the failing test**

```python
from pathlib import Path

from repoctx.telemetry import record_repoctx_invocation


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
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_telemetry.py -q`
Expected: FAIL because `repoctx.telemetry` does not exist yet.

**Step 3: Write minimal implementation**

Create `repoctx/telemetry.py` with:

- a helper that formats `event_time` in UTC with second resolution
- a helper that hashes query text and repo root with SHA-256
- `record_repoctx_invocation(...)`
- `record_agent_run(...)`
- a JSONL append helper that creates `~/.repoctx/telemetry` or an injected test directory on demand

Export the public recording helpers from `repoctx/__init__.py` only if the package already exposes symbols there.

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_telemetry.py -q`
Expected: PASS with one JSON object written to `repoctx-events.jsonl`.

**Step 5: Commit**

```bash
git add repoctx/telemetry.py repoctx/__init__.py tests/test_telemetry.py
git commit -m "feat: add local telemetry event writer"
```

### Task 2: Capture retriever metrics for telemetry

**Files:**
- Modify: `repoctx/models.py`
- Modify: `repoctx/retriever.py`
- Test: `tests/test_retriever.py`

**Step 1: Write the failing test**

Extend `tests/test_retriever.py` with assertions that `get_task_context_data(...)` returns enough metrics to populate telemetry, for example:

```python
assert result.metrics.files_selected == len(result.relevant_files)
assert result.metrics.docs_selected == len(result.relevant_docs)
assert result.metrics.tests_selected == len(result.related_tests)
assert result.metrics.neighbors_selected == len(result.graph_neighbors)
assert result.metrics.output_bytes > 0
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_retriever.py -q`
Expected: FAIL because `ContextResponse` does not expose telemetry-ready metrics yet.

**Step 3: Write minimal implementation**

Add a small metrics dataclass to `repoctx/models.py`, such as `ContextMetrics`, and attach it to `ContextResponse`. Populate it in `repoctx/retriever.py` with:

- `files_selected`
- `docs_selected`
- `tests_selected`
- `neighbors_selected`
- `output_bytes`

Keep the response payload backward-compatible by adding metrics to `to_dict()` in a way that does not disturb existing fields.

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_retriever.py -q`
Expected: PASS with the new metrics populated.

**Step 5: Commit**

```bash
git add repoctx/models.py repoctx/retriever.py tests/test_retriever.py
git commit -m "feat: expose telemetry-ready retriever metrics"
```

### Task 3: Emit telemetry from the CLI

**Files:**
- Modify: `repoctx/main.py`
- Create: `tests/test_main.py`
- Test: `tests/test_main.py`

**Step 1: Write the failing test**

Create `tests/test_main.py` with a test that runs `repoctx.main.main()` against a temporary repo and asserts a telemetry event is written:

```python
from pathlib import Path

from repoctx import main as repoctx_main


def test_cli_writes_repoctx_telemetry(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    telemetry_dir = tmp_path / ".telemetry"

    monkeypatch.setenv("REPOCTX_TELEMETRY_DIR", str(telemetry_dir))
    monkeypatch.setattr(
        "sys.argv",
        ["repoctx", "demo task", "--repo", str(tmp_path), "--format", "json"],
    )

    repoctx_main.main()

    assert (telemetry_dir / "repoctx-events.jsonl").exists()
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_main.py -q`
Expected: FAIL because the CLI does not emit telemetry yet.

**Step 3: Write minimal implementation**

Update `repoctx/main.py` to:

- measure wall time with a monotonic clock
- generate or accept `session_id`, `task_id`, and `variant`
- call `record_repoctx_invocation(...)` after successful runs
- write `error_type` and `success=False` on failures when possible
- keep telemetry best-effort so CLI output behavior does not change

Use an environment variable such as `REPOCTX_TELEMETRY_DIR` for test injection and local overrides.

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_main.py -q`
Expected: PASS with a valid `repoctx_invocation` event in the telemetry file.

**Step 5: Commit**

```bash
git add repoctx/main.py tests/test_main.py
git commit -m "feat: emit telemetry from cli runs"
```

### Task 4: Emit telemetry from MCP tool usage

**Files:**
- Modify: `repoctx/mcp_server.py`
- Modify: `tests/test_mcp_server.py`
- Test: `tests/test_mcp_server.py`

**Step 1: Write the failing test**

Extend `tests/test_mcp_server.py` with a case that invokes the registered tool and asserts a telemetry event is written to an injected telemetry directory.

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_mcp_server.py -q`
Expected: FAIL because MCP calls do not emit telemetry yet.

**Step 3: Write minimal implementation**

Update `repoctx/mcp_server.py` to:

- record `repoctx_invocation` events for each `get_task_context` call
- tag `surface="mcp"`
- reuse the same telemetry helpers as the CLI
- keep server behavior unchanged if telemetry writing fails

If needed, extend `create_server(...)` to accept an optional telemetry directory for tests.

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_mcp_server.py -q`
Expected: PASS with one telemetry event written for the MCP tool invocation.

**Step 5: Commit**

```bash
git add repoctx/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: emit telemetry from mcp usage"
```

### Task 5: Add downstream agent-run recording API

**Files:**
- Modify: `repoctx/telemetry.py`
- Modify: `README.md`
- Test: `tests/test_telemetry.py`

**Step 1: Write the failing test**

Extend `tests/test_telemetry.py` with a test for `record_agent_run(...)` that verifies:

- `agent-runs.jsonl` is created
- `task_id` and `variant` are preserved
- token and cost fields are written as numbers

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_telemetry.py -q`
Expected: FAIL because the agent-run helper is missing or incomplete.

**Step 3: Write minimal implementation**

Implement `record_agent_run(...)` in `repoctx/telemetry.py` with required fields for:

- `task_id`
- `variant`
- `runner`
- `agent_duration_ms`
- `prompt_tokens`
- `completion_tokens`
- `total_tokens`
- `estimated_cost_usd`
- optional `task_completed` and `quality_score`

Document the helper in `README.md` with one CLI example and one Python example so experiment harnesses can record control and treatment runs consistently.

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_telemetry.py -q`
Expected: PASS with both telemetry files containing valid JSONL entries.

**Step 5: Commit**

```bash
git add repoctx/telemetry.py README.md tests/test_telemetry.py
git commit -m "feat: add agent run telemetry api"
```

### Task 6: Run focused verification and full suite

**Files:**
- Test: `tests/test_telemetry.py`
- Test: `tests/test_main.py`
- Test: `tests/test_mcp_server.py`
- Test: `tests/test_retriever.py`
- Test: `tests/`

**Step 1: Run the focused telemetry tests**

Run: `python3 -m pytest tests/test_telemetry.py tests/test_main.py tests/test_mcp_server.py tests/test_retriever.py -q`
Expected: PASS.

**Step 2: Run the full suite**

Run: `python3 -m pytest -q`
Expected: PASS.

**Step 3: Review telemetry output manually**

Run a CLI invocation against a temporary repo and inspect the generated JSONL lines to confirm:

- second-resolution UTC timestamps
- hashed query and repo identifiers
- correct `surface` values
- correct `variant` values

**Step 4: Commit final verification changes if needed**

```bash
git add README.md repoctx tests
git commit -m "test: verify telemetry instrumentation"
```
