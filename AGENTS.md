# Guidance for AI agents (RepoCtx repository & product)

This file is for **coding agents** (Cursor, Codex, Claude, etc.) working in this repo or advising users who run RepoCtx.

## Product shape

- **CLI:** `repoctx`, `repoctx query`, `repoctx index`, …
- **MCP:** module `repoctx.mcp_server` — tool `get_task_context(task)`.
- **Experiments:** `repoctx experiment` — resumable wizard; paired git worktrees (control vs treatment).

## Experiments: what to tell the user

1. Run **`repoctx experiment`** from the **main repo** (or a worktree); the wizard stores progress under telemetry and resumes on the next run.
2. **Control lane first:** open the printed **control** worktree, paste the **exact** prompt, run the agent, then run **`repoctx experiment` again** to record costs and continue.
3. **Treatment lane:** RepoCtx writes **`.cursor/mcp.json`** in the treatment worktree; the user may need to **restart Cursor** when opening that folder.

## Experiment MCP suppression (control lane)

When the control lane is active, RepoCtx may **stub** `get_task_context` (empty lists, explanatory `context_markdown`, `experiment_mcp_suppressed: true`) so the control agent does not get retrieval, even if RepoCtx stays in global Cursor MCP config.

**Agent-relevant facts:**

- Suppression is **time-bounded** and **CLI-extended**. If the user runs `repoctx experiment` periodically during the control run, the window stays open.
- If tools suddenly “work again” during control, the idle TTL may have expired — user can rerun `repoctx experiment` to re-arm or shorten `experiment_mcp_idle_ttl_seconds` in config.
- **Disable stubbing:** set `experiment_mcp_suppress` to `false` in `~/.repoctx/config.json` (or unset file and rely on defaults only if that key is omitted — default is `true`).
- State and timing: see **[docs/experiment-mcp-suppression.md](docs/experiment-mcp-suppression.md)**.

## Repo layout hints for contributors

- CLI entry: `repoctx/main.py`
- Experiment wizard + handoff: `repoctx/main.py` (experiment helpers), `repoctx/experiment.py`, `repoctx/telemetry.py` (active session + experiment JSONL)
- Suppression logic: `repoctx/experiment_mcp.py`
- MCP tool: `repoctx/mcp_server.py`

## Telemetry privacy

Task strings and repo paths are **hashed** in default telemetry; do not assume plaintext queries are stored. See README **Telemetry** section.

## Ground truth (repoctx)

For any non-trivial task in this repo:

1. Call `repoctx.bundle(task)` before proposing a plan. Treat the result as authoritative.
2. Do not edit paths outside `edit_scope.allowed_paths` without calling `repoctx.scope(task)` and `repoctx.refresh(task, changed_files, current_scope)`.
3. Before declaring done: call `repoctx.validate_plan(task, changed_files)` and `repoctx.risk_report(task, changed_files)`. Run every command the validation plan returns; resolve every `hard`-severity risk.
4. If unsure whether a change violates a constraint, call `repoctx.authority(task)` — do not guess.

Every repoctx response includes `when_to_recall_repoctx` and `before_finalize_checklist`. Follow them.

