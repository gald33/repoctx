# RepoCtx

**Give your AI coding agent the right context, automatically.**

RepoCtx scans your repository and builds a focused context pack for any task — the relevant docs, source files, tests, and import neighbors your agent actually needs. No more pasting files manually or hoping the AI figures out your codebase.

```bash
pip install repoctx-mcp
repoctx "add retry jitter to webhook delivery" --repo ./my-project
```

## Why RepoCtx?

AI coding agents work better when they see the right files. RepoCtx does the file-finding for you:

- **Docs** — surfaces `AGENTS.md`, `README.md`, architecture guides, and convention files ranked by relevance to your task
- **Source files** — finds the code most related to what you're working on
- **Tests** — discovers matching test files so the agent can follow existing patterns
- **Import graph** — traces `import`/`require` statements to pull in closely connected modules

The output is a single Markdown pack (or JSON) you can feed directly to your agent.

## Quick Start

```bash
pip install "repoctx-mcp[mcp]"
```

Requires Python 3.11+.

**Using Cursor or another AI agent?** Skip ahead to [Setup for Cursor](#setup-for-cursor) — that's the most common path.

**Want to try it from the terminal first?** Run it directly:

```bash
repoctx "refactor the auth middleware to support OAuth" --repo ./my-app
```

This prints a ranked context pack to stdout. Add `--format json` for structured output.

## Setup for Cursor

Three steps. No server to run, no skill to write — Cursor manages everything once you add the config.

**1. Install with MCP support**

```bash
pip install "repoctx-mcp[mcp]"
```

**2. Open your MCP config**

In Cursor, open **Settings > MCP** and click **"Add new global MCP server"**. This opens a JSON config file. Add the `repoctx` entry:

```json
{
  "mcpServers": {
    "repoctx": {
      "command": "python3",
      "args": ["-m", "repoctx.mcp_server", "--repo", "/path/to/your-repo"]
    }
  }
}
```

Replace `/path/to/your-repo` with the absolute path to the repo you want the agent to understand.

**3. That's it — start using it**

Cursor starts and stops the server automatically. Your agent now has access to a `get_task_context` tool it can call to get relevant files for any task. You don't need to run anything in your terminal.

> **Tip:** If you want RepoCtx available for a specific project only, add the same config to `.cursor/mcp.json` in that project's root instead of using global settings.

## Setup for Claude Desktop & Other MCP Clients

The same MCP server works with any MCP-compatible client. The general pattern:

1. Install: `pip install "repoctx-mcp[mcp]"`
2. Register the server in your client's MCP config with the command `python3 -m repoctx.mcp_server --repo /path/to/your-repo`
3. The client manages the server lifecycle — you don't run it manually

The server exposes a single tool — `get_task_context(task)` — that the agent calls whenever it needs context.

## What You Get

For a task like `"add retry jitter to webhook delivery"` in a webhook project, RepoCtx returns:

```markdown
## Summary
Identified 2 docs, 2 files, 1 test, and 1 graph neighbor relevant to
'add retry jitter to webhook delivery'.

## Relevant Docs
- AGENTS.md — matches: retry, webhook
- docs/WEBHOOKS.md — matches: retry, webhook
  > Webhook delivery retries should use exponential backoff with jitter.

## Relevant Files
- src/webhook/retry_policy.py — matches: retry
  > def compute_retry_delay(): ...

## Related Tests
- tests/test_retry_policy.py — stem match + imports retry_policy.py

## Graph Neighbors
- src/webhook/delivery.py — imported by retry_policy.py
```

The JSON format includes the same sections with numeric scores, snippets, and a pre-rendered `context_markdown` field.

## Supported Languages & Files

| Category | Extensions |
|----------|-----------|
| Code | `.py` `.ts` `.tsx` `.js` `.jsx` |
| Config | `.json` `.yaml` `.yml` |
| Docs | `.md` `.mdc` |

Import graph tracing works for Python (`import`/`from`) and TypeScript/JavaScript (`import`/`require`).

## Configuration

RepoCtx works out of the box with sensible defaults. If you need to tune it, the main knobs are:

| Setting | Default | What it controls |
|---------|---------|-----------------|
| `max_docs` | 6 | Max doc files in context |
| `max_files` | 8 | Max source files in context |
| `max_tests` | 6 | Max test files in context |
| `max_neighbors` | 8 | Max import-graph neighbors |
| `max_file_bytes` | 16,000 | Max bytes read per file |

Directories like `.git`, `node_modules`, `venv`, `__pycache__`, and `dist` are ignored automatically.

## CLI Reference

```
repoctx <task> [options]
```

| Flag | Description |
|------|-------------|
| `--repo PATH` | Repository root (default: current directory) |
| `--format markdown\|json` | Output format (default: `markdown`) |
| `--verbose` | Enable debug logging |

You can also run it as a module:

```bash
python3 -m repoctx "your task" --repo ./your-repo
```

## Telemetry

RepoCtx writes local-only JSONL telemetry to `~/.repoctx/telemetry/`. All task text and repo paths are hashed before storage. No data is sent to any external service.

Set `REPOCTX_TELEMETRY_DIR` to change the storage location.

## Development

```bash
git clone https://github.com/gald33/repoctx.git
cd repoctx
pip install -e ".[dev]"
python3 -m pytest -q
```

## License

MIT
