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

### Install

```bash
pip install repoctx-mcp
```

Requires Python 3.11+.

### Run

Pass a task description and point at your repo:

```bash
repoctx "refactor the auth middleware to support OAuth" --repo ./my-app
```

Get JSON instead of Markdown:

```bash
repoctx "refactor the auth middleware to support OAuth" --repo ./my-app --format json
```

That's it. RepoCtx prints a ranked context pack to stdout.

## Use with Cursor, Claude & Other Agents (MCP)

RepoCtx ships an MCP server so AI tools can call it directly. Install the MCP extra:

```bash
pip install "repoctx-mcp[mcp]"
```

Start the server pointed at your repo:

```bash
python3 -m repoctx.mcp_server --repo /path/to/your-repo
```

The server exposes a single tool — `get_task_context(task)` — that agents call to get the context pack on demand.

### Cursor Setup

Add this to your MCP config so Cursor can use RepoCtx as a tool:

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
