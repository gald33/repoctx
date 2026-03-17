# RepoCtx

Local repository intelligence for coding agents.

## What It Does

`repoctx` inspects a local repository and returns a compact task context pack:

- relevant docs and agent guidance files
- relevant source files
- likely related tests
- nearby graph neighbors from local imports
- a readable markdown pack plus structured JSON

## Installation

Install the package from the standalone repository root:

```bash
python3 -m pip install -e .
```

This installs the core CLI without MCP server support.

Install MCP support when you want to run the MCP server:

```bash
python3 -m pip install -e ".[mcp]"
```

Install development dependencies for tests, including the MCP-related test coverage:

```bash
python3 -m pip install -e ".[dev]"
```

## CLI

Run the CLI against any local repository by passing its path with `--repo`:

```bash
repoctx "add retry jitter to webhook delivery" --repo /path/to/target-repo --format markdown
```

The module entry point stays available too:

```bash
python3 -m repoctx "Sync local env with Vercel" --repo /path/to/target-repo --format json
```

## MCP Server

Install the MCP extra first, then start the MCP server and point it at the repository you want to inspect:

```bash
python3 -m pip install -e ".[mcp]"
python3 -m repoctx.mcp_server --repo /path/to/target-repo
```

The server exposes `get_task_context(task: string)`.

## Telemetry

`repoctx` writes local JSONL telemetry to `~/.repoctx/telemetry` by default. CLI and MCP usage record `repoctx_invocation` events automatically. The default telemetry payload stores hashed free-form task text and hashed repo identifiers. CLI runs can also preserve explicit experiment keys such as `session_id`, `task_id`, and `variant`; MCP runs currently generate those identifiers automatically.

Use the CLI flags below to keep paired experiments aligned across control and treatment runs:

```bash
repoctx "add retry jitter to webhook delivery" \
  --repo /path/to/target-repo \
  --format json \
  --session-id bench-001 \
  --task-id task-001 \
  --variant repoctx
```

External experiment harnesses can record downstream agent costs with the Python helper:

```python
from pathlib import Path

from repoctx.telemetry import record_agent_run

record_agent_run(
    session_id="bench-001",
    task_id="task-001",
    variant="control",
    surface="cli",
    query="add retry jitter to webhook delivery",
    repo_root=Path("/path/to/target-repo"),
    runner="cursor-agent",
    success=True,
    completion_status="completed",
    agent_duration_ms=18420,
    tool_calls=6,
    prompt_tokens=12450,
    completion_tokens=1730,
    total_tokens=14180,
    estimated_cost_usd=0.19,
    task_completed=True,
    quality_score=0.9,
)
```

Use the same `session_id` and `task_id` for both `control` and `repoctx` runs so you can compare token, cost, and latency deltas later.

## Tests

```bash
python3 -m pytest -q
```
