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

## Ground-Truth Bundle (v2)

RepoCtx v2 adds an **authority-first** layer on top of the context pack. Instead of just "relevant files", an agent can ask for a Ground-Truth Bundle that includes:

- **Authority records** (Level 1: contracts/invariants; Level 2: AGENTS.md, architecture notes; Level 3: implementation)
- **Constraints** extracted from those records (front-matter + `## Invariants` / `## Do not` bullets + inline `INVARIANT:` / `DO NOT:` / `IMPORTANT:` markers)
- **Edit scope**: `allowed_paths`, `related_paths`, `protected_paths`
- **Validation plan**: tests and commands to run before finalizing
- **Risk notes**: protected-path touches, constraint violations, possible drift
- **Self-recall rules**: `when_to_recall_repoctx`, `before_finalize_checklist`, `uncertainty_rule` — always present, so the agent knows when to call RepoCtx again

### Protocol ops (CLI + MCP)

| Op | CLI | When to call |
|----|-----|--------------|
| `bundle(task)` | `repoctx bundle "task"` | Task start — primary call |
| `authority(task)` | `repoctx authority "task"` | Only need authority + constraints |
| `scope(task)` | `repoctx scope "task"` | Deciding what to edit |
| `validate_plan(task, changed_files)` | `repoctx validate-plan "task" --changed a.py b.py` | Before finalizing |
| `risk_report(task, changed_files)` | `repoctx risk-report "task" --changed a.py b.py` | Before finalizing |
| `refresh(task, changed_files, current_scope)` | `repoctx refresh "task" --changed a.py ...` | Scope expanded mid-task |

Target usage: **≤ 5 calls per task** in typical flows. Bundles are structured, authority-first, and token-budgeted.

### Try it

```bash
repoctx init-authority                   # scaffold contracts/ + docs/architecture/
repoctx bundle "refactor session-token storage"
repoctx bundle "refactor session-token storage" --format markdown
```

### Install for your agent harness

```bash
repoctx install                 # runs every harness installer + scaffolds authority layout
```

Or run individually if you only target one harness:

```bash
repoctx install-claude-code     # writes AGENTS.md section + .mcp.json
repoctx install-cursor          # writes AGENTS.md section + .cursor/mcp.json
repoctx install-codex           # writes AGENTS.md section + .codex/mcp.json
```

All installers are idempotent; existing AGENTS.md content and other MCP servers are preserved. Pass `--no-scaffold` to `repoctx install` to skip the contracts/docs/examples scaffold.

> **Note:** `repoctx install` does **not** build the embedding index. That's a separate step — see [Embedding-Based Retrieval](#embedding-based-retrieval-v2) below — because the embedding deps are an optional extra and the model download is ~1.2 GB.

### Repo conventions

RepoCtx v2 looks for (all optional, all lightweight):

- `AGENTS.md` / `AGENT.md` / `CLAUDE.md` — agent-facing instructions (Level 2)
- `contracts/**` — Level-1 contracts with YAML-ish front-matter (`applies_to`, `severity`, `validated_by`)
- `docs/architecture/**`, `docs/adr/**` — Level-2 architecture notes
- `examples/**` — validating examples
- `tests/contracts/**` — tests that enforce contracts
- Inline markers in any file: `# INVARIANT:`, `# CONTRACT:`, `# DO NOT:`, `# IMPORTANT:`, `# See contract: <path>`

Design doc: [`docs/plans/2026-04-23-repoctx-v2-design.md`](docs/plans/2026-04-23-repoctx-v2-design.md).

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

RepoCtx resolves the repo root in this order:

1. Per-call `repo_root` argument on the MCP tool — strongest override; the model can supply it directly. Once set, it is memoized for the lifetime of the MCP server process; subsequent calls may omit it.
2. `--repo /path/to/repo` flag (CLI / server startup).
3. `REPOCTX_REPO_ROOT` env var.
4. Host workspace env vars (`CLAUDE_PROJECT_DIR`, `WORKSPACE_FOLDER_PATHS`, `VSCODE_CWD`) — lets Cursor / Claude Code / Codex auto-scope without per-repo config.
5. `Path.cwd()` if it is not `/`.
6. `$PWD` env var — catches shell-launched cases where the host has chdir'd to `/` before exec.

Whatever candidate is chosen is then walked upward to the nearest `.git` entry (both `.git` directories and `.git` files are accepted, so linked worktrees and submodules work). If no git root is found, RepoCtx fails with a message that lists your most recently resolved repos so the agent can pick one and pass it as `repo_root`. RepoCtx never auto-selects from the recency list — that would silently pick the wrong repo when you work across several.

Nested repositories resolve to the **nearest** repo, not the outermost parent.

#### Claude Desktop note

Claude Desktop launches MCP servers via launchd, so the subprocess starts with cwd `/` and no workspace env vars. RepoCtx still boots cleanly; on the first tool call of a session the model needs to supply `repo_root` once (the tool descriptions remind it). After that, the session memo carries it for every subsequent call until you switch repos by passing a different `repo_root` explicitly. Claude Code, Cursor, and Codex don't need this — they already export workspace context.

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

This installs `sentence-transformers`, `numpy`, and the tree-sitter stack
(`tree-sitter` + `tree-sitter-language-pack`) used for symbol-aware chunking.
The model weights (~1.2 GB) are downloaded automatically on first use.

### Build the embedding index

Run from inside the repo (no `--repo` flag needed):

```bash
cd /path/to/repo
repoctx index
```

`--repo /path/to/repo` is only required when invoking from outside the repo directory.

The index command scans the repository, splits each file into overlapping chunks (symbol-aware for code — function/class/method boundaries; paragraph-aware for prose), embeds each chunk with metadata (`file:`, `kind:`, `module:`, `symbol:`, `lines:`), and writes the index to `.repoctx/embeddings/`. Add `.repoctx/` to your `.gitignore`.

> **Upgrading from a v1 index**: the on-disk format changed in 1.0.0 (`schema_version: 2`). Old indexes raise `IndexSchemaMismatch` on load. Delete `.repoctx/embeddings/` and re-run `repoctx index` once after upgrading.

> **Apple silicon (MPS) note**: chunk-aware indexing produces ~5× more rows per file than the old whole-file approach, which can blow the Metal allocator on larger repos. If you see a `Failed to allocate private MTLBuffer` error, force CPU encoding:
>
> ```bash
> REPOCTX_EMBEDDING_DEVICE=cpu repoctx index
> ```
>
> CPU is slower but reliable. You can also tune `REPOCTX_EMBEDDING_BATCH_SIZE` (default 16) downward if even CPU encoding hits memory limits.

### End-to-end first-time setup

```bash
pip install "repoctx-mcp[embeddings]"
cd /path/to/your/repo
repoctx install     # MCP wiring for Claude Code / Cursor / Codex + authority scaffold
repoctx index       # build the chunk-aware embedding index
```

After that, your agent gets context automatically through MCP. To query from the terminal:

```bash
repoctx "refactor the payment retry policy"
```

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

Default `embedding_weight` is 12.0. Files with cosine similarity above 0.3 bypass heuristic filters, so semantically relevant files surface even without keyword matches. When a file has multiple chunks, the file's cosine similarity is the **max** over its chunks — i.e. the score from the best-matching region of the file.

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

## Controlled Experiment Mode

RepoCtx can also run a guided `control` versus `treatment` comparison with paired git worktrees.

Start or resume the experiment with one command:

```bash
repoctx experiment
```

The first run launches a wizard that:

- collects one shared prompt for both lanes
- creates two clean worktrees from the same base commit under `.worktrees/`
- stores the exact prompt text and prompt hash for the session
- hands you off to the `control` worktree first

When you rerun `repoctx experiment`, RepoCtx resumes automatically:

- after the control run, it records the control costs and result fields, then prepares the `treatment` worktree
- for the treatment lane, it writes `.cursor/mcp.json` in that worktree so RepoCtx MCP is enabled there
- after the treatment run, it records the final lane and prints the summary automatically

Fast path is still available if you already know the shared prompt:

```bash
repoctx experiment "refactor the auth middleware to support OAuth"
```

The wizard asks for `cost before` and `cost after` for each lane so RepoCtx can calculate the delta for you.

### Experiment MCP suppression (control lane)

During the **control** lane, RepoCtx can **stub MCP tool results** (empty context + short message) so agents do not get RepoCtx retrieval even when RepoCtx remains registered in Cursor’s global `mcp.json`. Normal day-to-day use is unchanged; only the guided experiment arms this mode.

Timing is controlled with `~/.repoctx/config.json` (override the path with `REPOCTX_CONFIG_PATH` if needed):

| Key | Default | Meaning |
|-----|---------|---------|
| `experiment_mcp_suppress` | `true` | Set `false` to disable arming entirely (you then only get the legacy warning if global Cursor config still enables RepoCtx). |
| `experiment_mcp_idle_ttl_seconds` | `90` | Auto-clear suppression after this many seconds with **no** `repoctx` CLI activity (safety net if a run is abandoned). |
| `experiment_mcp_extend_seconds` | `600` | Each `repoctx` CLI run while suppression is active extends the deadline by this many seconds so long wizard sessions stay covered. |

State is stored next to telemetry (`~/.repoctx/telemetry/experiment-mcp-suppress.json` unless `REPOCTX_TELEMETRY_DIR` is set). Suppression is cleared when you **record a lane**, **start the treatment handoff**, or when the idle deadline passes (checked on every MCP tool call and every CLI entry).

More detail: **[docs/experiment-mcp-suppression.md](docs/experiment-mcp-suppression.md)**. For **AI agents** working in this repo or advising users: **[AGENTS.md](AGENTS.md)**.

Example summary:

```text
Experiment summary
Task: refactor the auth middleware to support OAuth
Session: abc123
Base commit: 7f2c9a1
Prompt hash: 6f...

control
before: $12.41
after:  $12.89
delta:  $0.48
files changed: 3
lines added/deleted: 18/4
completion: completed
verification: passed

treatment
before: $12.89
after:  $13.02
delta:  $0.13
files changed: 2
lines added/deleted: 11/3
completion: completed
verification: passed

difference
treatment saved: $0.35
treatment saved: 72.9%
winner: treatment
```

What the experiment measures:

- manual before/after total cost checkpoints from your agent UI
- git-derived change statistics from each isolated worktree
- optional completion and verification status you provide when recording a lane

Controlled experiment assumptions:

- both lanes must use the exact same prompt
- both lanes start from the same base commit
- each lane runs in its own worktree
- cost is entered manually from the external agent UI

Current limitations:

- RepoCtx does not measure external agent time automatically
- quality is not scored automatically
- cost accuracy depends on the numbers you enter for each lane

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
