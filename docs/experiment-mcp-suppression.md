# Experiment MCP suppression (human reference)

This document describes how RepoCtx isolates the **control** lane of `repoctx experiment` from real RepoCtx MCP retrieval, without editing `~/.cursor/mcp.json`.

## Why it exists

Many users keep RepoCtx enabled globally in Cursor. For a fair control-vs-treatment experiment, the control agent should not receive RepoCtx context. Because Cursor loads MCP from user config, RepoCtx instead **short-circuits** the MCP tool: `get_task_context` returns an empty stub and a short message while suppression is active.

## What it does *not* do

- It does not remove RepoCtx from Cursor’s server list.
- It does not guarantee isolation from other MCP servers.
- It is **best-effort**: accuracy trade-off for simpler UX versus editing global JSON.

## Configuration (`~/.repoctx/config.json`)

Override the path with **`REPOCTX_CONFIG_PATH`**.

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `experiment_mcp_suppress` | bool | `true` | When `false`, suppression is never armed; you only get legacy warnings if RepoCtx is globally enabled. |
| `experiment_mcp_idle_ttl_seconds` | number | `90` | After this many seconds **without** any `repoctx` CLI invocation extending the window, suppression auto-clears (safety net). |
| `experiment_mcp_extend_seconds` | number | `600` | Each **`repoctx` CLI** run while suppression is active pushes the deadline forward by this amount. |

Example:

```json
{
  "experiment_mcp_suppress": true,
  "experiment_mcp_idle_ttl_seconds": 120,
  "experiment_mcp_extend_seconds": 900
}
```

## State file

Suppression state is written next to telemetry:

- Default directory: `~/.repoctx/telemetry/`
- Filename: `experiment-mcp-suppress.json`
- Override directory: **`REPOCTX_TELEMETRY_DIR`**

The file stores `suppressed`, `until_unix`, and `armed_at_unix`. You may delete `experiment-mcp-suppress.json` manually to force MCP tools to behave normally.

## Lifecycle

1. **Armed** when the wizard prints the control-lane handoff or a control-lane resume reminder.
2. **Extended** on every `repoctx` ... CLI entry (after parse) while still active.
3. **Checked** on every MCP `get_task_context` call: if `now >= until_unix`, suppression is cleared.
4. **Cleared** when you record any experiment lane, when the treatment handoff runs (treatment expects real RepoCtx), or when idle TTL elapses.

## Related docs

- User-oriented overview: [README.md](../README.md) → section **Controlled Experiment Mode** → **Experiment MCP suppression**.
- Man page: `docs/man/repoctx.1` (`experiment` command).
