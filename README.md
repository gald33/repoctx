# RepoCtx

**Give your coding agent the right files for the task at hand.**

RepoCtx scans a local repository and returns a focused context pack: the docs, source files, tests, and import neighbors most relevant to a task like "add retry jitter to webhook delivery" or "refactor auth middleware for OAuth".

It is built for developers using tools like Cursor, Claude Desktop, and Codex who want better results without manually pasting half their repo into chat.

## Why Developers Use It

When an AI agent misses the right files, it guesses. RepoCtx reduces that guesswork by surfacing:

- relevant docs like `AGENTS.md`, `README.md`, and architecture notes
- relevant source files for the task
- likely related tests
- nearby modules from the local import graph

The result is a compact Markdown pack or JSON payload your agent can use directly.

## Start Here

RepoCtx is primarily used through MCP clients like Cursor, Claude Desktop, and Codex.

Install it with:

```bash
python3 -m pip install repoctx-mcp
```

Requires Python 3.11+.

Important naming note:

- the package name is `repoctx-mcp`
- the CLI command is `repoctx`
- the Python module name is also `repoctx`

If you use Cursor, the normal path is:

1. install `repoctx-mcp`
2. add the MCP config below
3. restart Cursor
4. use your agent normally

You do not need to manually run the MCP server in a terminal for normal Cursor use.

If you are here for the default setup, continue with the Cursor section below and paste the config as-is.

## 5-Minute Setup

### Cursor

If you use Cursor, this is the default path.

**1. Add RepoCtx to your MCP config**

Use one of these locations:

- global config: `~/.cursor/mcp.json`
- project config: `.cursor/mcp.json`

You can also add the same server through Cursor's **Tools & MCP** settings UI, but the JSON file below is the most direct copy-paste path.

Paste this into one of those files:

```json
{
  "mcpServers": {
    "repoctx": {
      "command": "python3",
      "args": ["-m", "repoctx.mcp_server"]
    }
  }
}
```

That is the normal setup. RepoCtx will use the startup path the MCP client gives it and automatically resolve to the nearest enclosing git root. Add `--repo /path/to/repo` only if you need to pin Cursor to a specific repository instead of using that automatic behavior.

**2. Restart Cursor**

Cursor loads MCP servers from `mcp.json` when it starts.

**3. Use your agent normally**

Ask Cursor to work on a task in that repo. RepoCtx shows up as an MCP tool, and Cursor can call it when it needs context.

**What you do not need to do**

- You do **not** need to run `python3 -m repoctx.mcp_server ...` yourself.
- You do **not** need to write a custom skill.
- You do **not** need to manually paste repo files into chat.

### Claude Desktop

Claude Desktop can use the same RepoCtx MCP server.

**1. Open the Claude Desktop MCP config**

In Claude Desktop, open **Settings > Developer > Edit Config**.

Common config locations:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

RepoCtx is intended for the Claude Desktop app, not the web app.

**2. Add RepoCtx**

```json
{
  "mcpServers": {
    "repoctx": {
      "command": "python3",
      "args": ["-m", "repoctx.mcp_server"]
    }
  }
}
```

RepoCtx will use the startup path the MCP client gives it and automatically resolve to the nearest enclosing git root. Add `--repo /path/to/repo` only if you want Claude Desktop pinned to one repository.

**3. Restart Claude Desktop**

After restart, Claude can call RepoCtx as a tool when it needs repository context.

### Codex

Codex supports MCP in both the CLI and the IDE extension. They share the same config.

**Option A: Add RepoCtx to `config.toml`**

Use one of these locations:

- global config: `~/.codex/config.toml`
- project config: `.codex/config.toml` in a trusted project

Add:

```toml
[mcp_servers.repoctx]
command = "python3"
args = ["-m", "repoctx.mcp_server"]
```

**Option B: Add it from the Codex CLI**

```bash
codex mcp add repoctx -- python3 -m repoctx.mcp_server
```

You can inspect configured servers with:

```bash
codex mcp list
```

If you use the Codex IDE extension, it will read the same MCP configuration.

RepoCtx will use the startup path the MCP client gives it and automatically resolve to the nearest enclosing git root. Add `--repo /path/to/repo` only if you want Codex pinned to one repository.

## What To Ask Your Agent

Once RepoCtx is configured, you can ask your client to do normal development work, for example:

- "Add retry jitter to webhook delivery."
- "Refactor the auth middleware to support OAuth."
- "Find the files involved in syncing local env with Vercel."
- "Show me the tests related to the billing webhook flow."

RepoCtx helps the agent find the most relevant files before it starts editing.

## What RepoCtx Returns

For a task like `"add retry jitter to webhook delivery"`, RepoCtx returns a focused pack like:

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

Use `--format json` if you want structured output instead of Markdown.

## FAQ

### Do I need to run a server manually?

No, not in Cursor, Claude Desktop, or Codex. Those clients start the RepoCtx MCP server for you from the config you provide.

You would only run `python3 -m repoctx.mcp_server` yourself if you were debugging the server directly.

### Do I need to write a skill?

No. RepoCtx is an MCP server, not a skill. Once your client is configured, it becomes an available tool the agent can call.

### Do I need one config per repo?

Not necessarily.

- Use a global config if you want RepoCtx available everywhere.
- Use a project config if you want RepoCtx tied to one repo and shared with teammates.

### How does RepoCtx choose the repo automatically?

By default, RepoCtx uses the startup path from the MCP client and resolves it to the nearest enclosing git root. In practice, that means if the client starts RepoCtx inside a nested repository, RepoCtx focuses on that nested repo rather than walking up to a larger parent checkout.

If you want to override that automatic choice, pass `--repo /path/to/repo`.

### Can I test RepoCtx from the terminal first?

Yes. RepoCtx also works as a normal CLI for terminal testing or non-MCP usage.

```bash
cd my-app
repoctx "refactor the auth middleware to support OAuth"
```

## CLI Usage (Optional)

Use this section if you want to test RepoCtx from the terminal or use it without an MCP client.

If you want to use RepoCtx outside an MCP client:

```bash
python3 -m pip install repoctx-mcp
cd /path/to/repo
repoctx "your task"
```

JSON output:

```bash
repoctx "your task" --format json
```

Module entry point:

```bash
python3 -m repoctx "your task"
```

CLI flags:

| Flag | Description |
|------|-------------|
| `--repo PATH` | Optional repository root override |
| `--format markdown\|json` | Output format |
| `--verbose` | Enable debug logging |
| `--debug-scores` | Print heuristic/embedding/final score breakdown |
| `--no-embeddings` | Disable embedding retrieval for this query |

## Embedding-Based Retrieval (v2)

RepoCtx v2 adds optional local embeddings using [Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) to improve recall when your task description doesn't match filenames or code tokens.

Embeddings are additive — the existing heuristic ranking (token overlap, doc priority, graph expansion) still runs. Embedding similarity scores are blended in as a boost, and files with strong semantic similarity can surface even without token overlap.

### Install embedding dependencies

```bash
pip install "repoctx-mcp[embeddings]"
```

This installs `sentence-transformers` and `numpy`. The model weights (~1.2 GB) are downloaded automatically on first use.

### Build the embedding index

```bash
repoctx index --repo /path/to/repo
```

This scans the repository, embeds every file (with enriched metadata), and writes the index to `.repoctx/embeddings/` inside the repo. Add `.repoctx/` to your `.gitignore`.

### Query with hybrid retrieval

Once the index exists, all queries automatically use hybrid retrieval:

```bash
repoctx "refactor payment processing" --repo /path/to/repo
```

To see the score breakdown:

```bash
repoctx query "refactor payment processing" --repo /path/to/repo --debug-scores
```

### Update a single file

After editing a file, you can re-embed just that file:

```bash
repoctx update src/billing/invoice.py --repo /path/to/repo
```

### Rebuild the index from scratch

```bash
repoctx rebuild --repo /path/to/repo
```

### How hybrid scoring works

For each candidate file, the final score is:

```
final_score = heuristic_score + embedding_weight × max(0, cosine_similarity)
```

Default `embedding_weight` is 12.0. Files with cosine similarity above 0.3 bypass heuristic filters, so semantically relevant files surface even without keyword matches.

### Fallback behavior

If embedding dependencies are not installed or no index exists, RepoCtx silently falls back to pure heuristic retrieval. The MCP tool contract is unchanged — `get_task_context(task)` always works.

## Supported Files

| Category | Extensions |
|----------|-----------|
| Code | `.py`, `.ts`, `.tsx`, `.js`, `.jsx` |
| Config | `.json`, `.yaml`, `.yml` |
| Docs | `.md`, `.mdc` |

Import graph expansion works for Python (`import`, `from`) and JavaScript/TypeScript (`import`, `require`).

## Telemetry

RepoCtx writes local JSONL telemetry to `~/.repoctx/telemetry/` by default. Task text and repo identifiers are hashed before storage. Set `REPOCTX_TELEMETRY_DIR` to change the storage location.

## Development

```bash
git clone https://github.com/gald33/repoctx.git
cd repoctx
python3 -m pip install -e ".[dev]"
python3 -m pytest -q
```

To develop with embedding support:

```bash
python3 -m pip install -e ".[dev,embeddings]"
```

## License

MIT
