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

## Tests

```bash
python3 -m pytest -q
```
