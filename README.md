# RepoCtx

**Give your coding agent the ground truth for the task at hand.**

A local MCP server that, for each task, hands your agent the docs, contracts, invariants, in-scope files, related tests, and import-graph neighbors that actually matter — so the model anchors on what's real instead of guessing.

Works with Cursor, Claude Desktop, Claude Code, Codex, and any MCP-speaking client.

> **AI agents working in this repo or advising users on RepoCtx:** start with **[AGENTS.md](AGENTS.md)**.

---

## The problem

A few prompts into a task, agents drift. They edit unrelated files, add "small improvements" that pull them away from the goal, and break invariants nobody told them about. Not because the model is bad — it just doesn't know which docs to trust, what's load-bearing, or where the task ends.

That part doesn't have to be guessed every turn. It can be prepared once and handed to the agent up front.

## What RepoCtx does

For a task like *"add retry jitter to webhook delivery"*, RepoCtx returns a **ground-truth bundle**:

| Layer | What you get |
|-------|--------------|
| **Authoritative docs** | `AGENTS.md`, `README.md`, architecture notes, ADRs |
| **Contracts & invariants** | `contracts/**`, plus inline `INVARIANT:` / `DO NOT:` / `IMPORTANT:` markers |
| **In-scope source files** | The files the task actually touches (heuristics + embeddings) |
| **Related tests** | Tests likely to cover what you're editing |
| **Graph neighbors** | Nearby modules from the local import graph |
| **Edit scope** | `allowed_paths`, `related_paths`, `protected_paths` |
| **Validation plan** | Tests and commands to run before finalizing |
| **Risk notes** | Protected-path touches, constraint violations, possible drift |

The bundle is compact, token-budgeted, Markdown or JSON.

---

## Install

```bash
pip install "repoctx-mcp[embeddings]"
```

Requires Python 3.11+. The embeddings extra downloads a ~1.2 GB local model on first use (Qwen3-Embedding-0.6B). For a lighter install without semantic retrieval:

```bash
pip install repoctx-mcp
```

> **Naming note:** the package is `repoctx-mcp`, the CLI command is `repoctx`, the Python module is also `repoctx`.

Then, from inside the repo you want to use it on:

```bash
repoctx install
```

This single command:

- wires RepoCtx into Claude Code, Cursor, and Codex (whichever configs exist locally or globally)
- scaffolds a starter authority layout (`contracts/`, `docs/architecture/`)
- builds the embedding index if `[embeddings]` is installed

Flags: `--no-index` skips the embedding build, `--with-index` requires it (errors if extras are missing), `--no-scaffold` skips the contracts/docs scaffold.

You can also install for one client at a time: `repoctx install-claude-code`, `repoctx install-cursor`, `repoctx install-codex`. All installers are idempotent — existing config and other MCP servers are preserved.

---

## Editor setup

`repoctx install` writes these for you, but if you'd rather configure manually:

### Cursor

Add to `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (per-project):

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

Restart Cursor. RepoCtx auto-resolves to the nearest git root from Cursor's workspace context.

### Claude Desktop

Open **Settings → Developer → Edit Config** (or edit `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, `%APPDATA%\Claude\claude_desktop_config.json` on Windows):

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

Restart Claude Desktop. Because Claude Desktop launches MCP servers via launchd (cwd `/`, no workspace env vars), the first tool call of a session needs to supply `repo_root` once. The session memo carries it for every call after that.

### Codex

Add to `~/.codex/config.toml` or `.codex/config.toml`:

```toml
[mcp_servers.repoctx]
command = "python3"
args = ["-m", "repoctx.mcp_server"]
```

Or from the CLI:

```bash
codex mcp add repoctx -- python3 -m repoctx.mcp_server
```

### Claude Code

`repoctx install` writes `.mcp.json` and adds an `AGENTS.md` / `CLAUDE.md` nudge so the agent knows to call `bundle` at task start. It also wires up three hooks (see [Claude Code hook integration](#claude-code-hook-integration) below).

---

## What to ask your agent

Just describe the task normally:

- *"Add retry jitter to webhook delivery."*
- *"Refactor the auth middleware to support OAuth."*
- *"Find the files involved in syncing local env with Vercel."*
- *"Show me the tests related to the billing webhook flow."*

The agent calls RepoCtx as a tool and uses the bundle to scope its work.

## Example output

For `"add retry jitter to webhook delivery"`:

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

Pass `--format json` for structured output.

---

## MCP tools

The full protocol — call these from any MCP client. Target usage is **≤ 5 calls per task**.

| Tool | CLI | When to call |
|------|-----|--------------|
| `bundle(task)` | `repoctx bundle "task"` | Task start — primary call |
| `authority(task)` | `repoctx authority "task"` | Just need authority + constraints |
| `scope(task)` | `repoctx scope "task"` | Deciding what to edit |
| `validate_plan(task, changed_files)` | `repoctx validate-plan "task" --changed a.py b.py` | Before finalizing |
| `risk_report(task, changed_files)` | `repoctx risk-report "task" --changed a.py b.py` | Before finalizing |
| `refresh(task, changed_files, current_scope)` | `repoctx refresh "task" --changed a.py ...` | Scope expanded mid-task |
| `semantic_search(query, top_k, kind)` | `repoctx semantic-search "query" --top 10` | Raw similarity lookup, no bundling |
| `advisory_search(query, top_k)` | `repoctx advisory-search "query"` | "Is this being built on another branch?" (opt-in, lower authority) |

Every bundle also carries self-recall rules — `when_to_recall_repoctx`, `before_finalize_checklist`, `uncertainty_rule` — so the agent knows when to call back without you reminding it.

Every `bundle` / `semantic_search` response also reports **retrieval health**. `bundle` carries a top-level `warnings[]` and a `retrieval` block (`ranker`, `index_status`, `index_location`); `semantic_search` returns an envelope `{status, message, results}`. If `index_status` isn't `ok` (e.g. `no_index`), embedding retrieval is **dark** and results are lexical-only — the warning says how to fix it. An empty `results` with `status: no_index` is *not* "no matches".

(A legacy `get_task_context(task)` entry point is still supported — see the [appendix](#legacy-and-migration-notes).)

---

## Authority conventions

RepoCtx looks for the following (all optional, all lightweight):

| Layer | Where | What |
|-------|-------|------|
| **Level 1 — Contracts** | `contracts/**` | YAML front-matter (`applies_to`, `severity`, `validated_by`) + `## Invariants` / `## Do not` sections |
| **Level 2 — Agent docs** | `AGENTS.md`, `AGENT.md`, `CLAUDE.md` | Agent-facing instructions |
| **Level 2 — Architecture** | `docs/architecture/**`, `docs/adr/**` | Architecture notes, ADRs |
| **Examples** | `examples/**` | Validating examples |
| **Contract tests** | `tests/contracts/**` | Tests that enforce contracts |
| **Inline markers** | Any file | `# INVARIANT:`, `# CONTRACT:`, `# DO NOT:`, `# IMPORTANT:`, `# See contract: <path>` |

Bootstrap with `repoctx init-authority` to scaffold `contracts/` and `docs/architecture/` with starter templates.

`repoctx propose-authority` scans your repo and suggests candidates — files that look load-bearing based on imports, references, and patterns — so you don't have to declare them by hand.

Design doc: [`docs/plans/2026-04-23-repoctx-v2-design.md`](docs/plans/2026-04-23-repoctx-v2-design.md).

---

## Claude Code hook integration

`repoctx install` (and `repoctx install-claude-code`) adds three hooks to `.claude/settings.json` that nudge the agent to actually use RepoCtx:

| Hook | When | What it does |
|------|------|--------------|
| `UserPromptSubmit` | Task entry | For substantive prompts, reminds the agent to call `mcp__repoctx__bundle` before planning |
| `PostToolUse` matching `Edit\|Write\|MultiEdit` | After every edit | Keeps the embedding index live (`repoctx update --from-claude-hook`) |
| `Stop` | Task exit | If the turn made edits but didn't call `validate_plan`, reminds the agent to run it |

Both nudges read Claude Code's hook JSON from stdin and **always exit 0** — they never block the user's flow. The entry nudge stays silent for short, keyword-free prompts. The exit nudge stays silent when there were no edits, or when `validate_plan` was already called this turn. Hook entries are detected by command prefix on re-install, so re-running `repoctx install` is safe.

The anchored `<!-- repoctx-nudge -->` block placed in `CLAUDE.md` / `AGENTS.md` ships with current wording (must-call + inline definition of "non-trivial"). Older anchored blocks from earlier installs are rewritten in place on the next `install` / `refresh` without touching surrounding doc content.

**Tuning / opt-out:**

- `REPOCTX_LEARN=1` — appends *"If you decide to skip this, briefly state the reason."* to reminders so you can collect skip rationale during a tuning week. Off by default.
- `--no-claude-md-nudge` or `REPOCTX_NO_CLAUDE_MD_NUDGE=1` — skip the anchored-block insertion.
- To fully opt out: delete the entries from `.claude/settings.json`. They won't be re-added unless you re-run the install command.

---

## Embeddings (optional, on by default)

RepoCtx blends three signals — heuristic token overlap, doc priority + graph expansion, and local embeddings — to rank candidate files. The embedding layer uses [Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) and improves recall when the task description shares no tokens with relevant filenames.

```bash
pip install "repoctx-mcp[embeddings]"   # adds sentence-transformers, numpy, tree-sitter
cd /path/to/repo
repoctx install                          # builds index automatically
```

Add `.repoctx/` to `.gitignore` (it holds per-repo config, feedback logs, and — for non-git dirs — the index).

**Manual control:**

```bash
repoctx index                       # build the index (from origin/main by default)
repoctx index --source worktree     # index the current working tree instead
repoctx index --refresh             # fetch origin/main + re-embed the delta
repoctx update src/billing/foo.py   # re-embed a single file
repoctx rebuild                     # rebuild from scratch
```

The indexer splits each file into overlapping chunks — symbol-aware for code (function/class/method boundaries), paragraph-aware for prose — and stores them with metadata (`file:`, `kind:`, `module:`, `symbol:`, `lines:`).

### Where the index lives (worktree-aware)

The index is keyed by **repo identity**, not by the directory you ran from. It's stored under the repository's shared git common dir — `<git-common-dir>/repoctx/embeddings` (typically `<repo>/.git/repoctx/embeddings`) — which **every linked worktree and the main checkout resolve to identically**. Build it once from any worktree and it's found from all of them. Because it lives under `.git/`, it never shows up as dirty/untracked and is never committed. (Repos without git fall back to the legacy in-tree `<repo>/.repoctx/embeddings`.)

Pre-1.5 in-tree indexes are migrated automatically: the first read or `repoctx index` relocates `<repo>/.repoctx/embeddings` to the shared location (it's read in place if the move can't happen).

### Pinned to origin/main, refreshed on a TTL

The authoritative index reflects **`origin/main`** — landed work — read directly from git objects (`git ls-tree`/`cat-file`), independent of whatever branch a worktree is on. This matters for repos that squash-merge and delete branches: mid-session `origin/main` advances and merged branches vanish, so the ground truth lives only in main.

- A `git fetch origin main` is **TTL-gated** (default 30 min; `REPOCTX_BASE_FETCH_TTL_SECONDS`) so it isn't paid on every call.
- When origin/main has advanced past the indexed base, the next read re-embeds just the delta (incremental, capped). Set `REPOCTX_BASE_REFRESH_ON_READ=0` for warn-only — you'll get a `warnings[]` entry pointing at `repoctx index --refresh` instead of a synchronous re-embed.

### Your in-progress work is overlaid

On top of the origin/main base, the **current worktree's delta** — commits ahead of origin/main (`merge-base..HEAD`) plus uncommitted/untracked edits — is layered in at query time. So retrieval models *"fresh origin/main ∪ my in-progress work"* (what the tree will look like after a rebase) without you rebasing. Only the delta is embedded per query (cached by content hash). Disable with `REPOCTX_OVERLAY_WORKTREE=0`.

### Advisory lane (opt-in): in-flight branches

A separate, **strictly lower-authority** lane answers *"is this already being built elsewhere?"* by indexing committed branch tips **ahead of origin/main** (recent, unmerged, deduped; read from git objects — never sibling worktrees' uncommitted bytes). It's **off by default**:

```bash
repoctx advisory-index                       # build it (opt-in)
repoctx advisory-search "rate limiter"       # query it
repoctx bundle "task" --include-advisory     # attach hits under a separate `advisory` key
```

Advisory hits are tagged with provenance (branch, commits-ahead, last-commit date, merge status) and are **never** folded into a bundle's `relevant_code` / `authority` / `constraints`.

**Scoring:**

```
final_score = heuristic_score + embedding_weight × max(0, cosine_similarity)
```

Default `embedding_weight` is 12.0. Files with cosine similarity above 0.3 bypass heuristic filters, so semantically relevant files surface even without keyword overlap. For multi-chunk files, the file's similarity is the **max** over its chunks.

**Tunables (env vars):**

| Var | Default | Notes |
|-----|---------|-------|
| `REPOCTX_EMBEDDING_DEVICE` | `auto` | `cpu` / `cuda` / `mps` / `auto` |
| `REPOCTX_EMBEDDING_BATCH_SIZE` | `16` | Clamped to 8 on MPS |
| `REPOCTX_EMBEDDING_MAX_SEQ_LENGTH` | `256` | |
| `REPOCTX_EMBEDDING_DTYPE` | `auto` | `fp16` on accelerators, `fp32` on CPU |
| `REPOCTX_BASE_FETCH_TTL_SECONDS` | `1800` | How often the read path may `git fetch origin main` |
| `REPOCTX_BASE_REFRESH_ON_READ` | `1` | `0` = warn-only on origin/main drift (no on-read re-embed) |
| `REPOCTX_OVERLAY_WORKTREE` | `1` | `0` = retrieval reflects pure origin/main (no worktree overlay) |

> **Apple silicon (MPS):** indexing handles GPU memory automatically (fp16, `max_seq_length=256`, batch clamped to 8, cache eviction between super-batches). Catchable encode errors fall back to CPU transparently. The rare uncatchable Metal C++ assertion still requires `REPOCTX_EMBEDDING_DEVICE=cpu repoctx index`.

**Fallback is loud, not silent.** If embedding dependencies aren't installed or no index exists, RepoCtx falls back to heuristic (lexical) retrieval — but it *says so*: `bundle` adds a top-level `warnings[]` entry and sets `retrieval.index_status`; `semantic_search` returns `status: no_index` (not a bare empty list). The fix (`repoctx index`) is in the message. The MCP tool contract is otherwise unchanged.

> Migrating an older index? See [Legacy and migration notes](#legacy-and-migration-notes) in the appendix.

---

## CLI reference

The CLI works standalone too — useful for terminal testing or non-MCP usage.

```bash
cd my-app
repoctx "your task"                        # default: query
repoctx "your task" --format json
repoctx query "your task" --debug-scores   # show heuristic/embedding/final breakdown
```

| Flag | Description |
|------|-------------|
| `--repo PATH` | Repository root override |
| `--format markdown\|json` | Output format |
| `--verbose` | Debug logging |
| `--debug-scores` | Print score breakdown |
| `--no-embeddings` | Disable embedding retrieval for this query |

**Subcommands** (`repoctx COMMAND --help` for each):

- `query` (default), `bundle`, `authority`, `scope`, `validate-plan`, `risk-report`, `refresh`, `detect-changes`, `semantic-search`
- `advisory-index`, `advisory-search` (opt-in in-flight-branch lane)
- `index` (`--source origin-main|worktree`, `--refresh`), `update`, `rebuild`
- `install`, `install-claude-code`, `install-cursor`, `install-codex`
- `init-authority`, `propose-authority`
- `experiment`, `hook`, `stats`, `reporting`

---

## Configuration

### Repo root resolution

RepoCtx resolves the repo root in this order:

1. Per-call `repo_root` argument on the MCP tool — strongest override; memoized for the lifetime of the MCP server process.
2. `--repo /path/to/repo` flag (CLI / server startup).
3. `REPOCTX_REPO_ROOT` env var.
4. Host workspace env vars (`CLAUDE_PROJECT_DIR`, `WORKSPACE_FOLDER_PATHS`, `VSCODE_CWD`).
5. `Path.cwd()` if it is not `/`.
6. `$PWD` env var.

Whatever candidate is chosen is then walked upward to the nearest `.git` entry (both `.git` directories and `.git` files are accepted, so linked worktrees and submodules work). Nested repositories resolve to the **nearest** repo, not the outermost parent.

If no git root is found, RepoCtx fails with a message listing your most recently resolved repos so the agent can pick one and pass it as `repo_root`. RepoCtx never auto-selects from the recency list.

### Supported files

| Category | Extensions |
|----------|-----------|
| Code | `.py`, `.ts`, `.tsx`, `.js`, `.jsx` |
| Config | `.json`, `.yaml`, `.yml` |
| Docs | `.md`, `.mdc` |

Import-graph expansion works for Python (`import`, `from`) and JavaScript/TypeScript (`import`, `require`).

### Telemetry

RepoCtx writes local JSONL telemetry to `~/.repoctx/telemetry/` by default. Task text and repo identifiers are hashed before storage. Set `REPOCTX_TELEMETRY_DIR` to change the location. **Local telemetry never leaves your machine** unless you opt in to reporting (see below).

Index builds are timed too: `repoctx index` / `rebuild` print a one-line breakdown (`model load` vs `embed` vs `scan`) and record an `index_build` event. Run `repoctx stats` to see the model-load-vs-embed split and per-build corpus size aggregated across builds — useful for deciding whether a slow build is the one-time model load or the repo-sized embed.

### Anonymous reporting (opt-in on stable, default-on on canary)

To help tune retrieval, RepoCtx can upload a *redacted* subset of telemetry events (counts, timings, error classes — see below) to a maintainer-run ingest endpoint. The privacy contract:

- **Stable builds (`pip install repoctx-mcp`): OFF by default.** No prompt, ever. You opt in explicitly with `repoctx reporting on`.
- **Canary builds (`pip install --pre repoctx-mcp`): ON by default**, with a one-time disclosure printed to stderr on first run.
- **Kill switch:** `REPOCTX_REPORTING=off` overrides everything.
- **What's sent:** `op`, `success`, `duration_ms`, `output_bytes`, `error_type` (class only, never the message), per-op `stats` (e.g. files considered/selected), plus an anonymous `install_id` (random UUID), the channel tag, and a `repo_fingerprint = sha256(install_id || first_commit_sha)` that's stable per (install, repo) but not correlatable across users.
- **What's never sent:** paths, query/task text, code, error messages, git remote URLs, hostnames, usernames. The ingest endpoint enforces this independently and rejects events containing any forbidden key.

Inspect or toggle anytime:

```bash
repoctx reporting status        # show current state, channel, install_id, queue size
repoctx reporting on            # enable
repoctx reporting off [--purge] # disable; --purge also drops any pending queue
repoctx reporting show          # print queued events that would be uploaded
repoctx reporting flush         # attempt upload now
```

The MCP server exposes the same toggle as a `reporting` tool, so an agent collaborating with you can inspect or flip the flag on your behalf.

---

## FAQ

**Do I need to run a server manually?**
No. Cursor, Claude Desktop, Claude Code, and Codex all start the MCP server for you from the config. You'd only run `python3 -m repoctx.mcp_server` if you were debugging the server itself.

**Do I need to write a skill?**
No. RepoCtx is an MCP server, not a skill. Once your client is configured, it becomes a tool the agent can call.

**Do I need one config per repo?**
Either works. Use a global config if you want RepoCtx everywhere; use a project config if you want it tied to one repo and shared with teammates.

**How does RepoCtx pick the repo automatically?**
See [Repo root resolution](#repo-root-resolution).

**Can I test from the terminal first?**
Yes — `repoctx "your task"` from inside any repo.

---

## Appendix

### Controlled experiment mode

RepoCtx can run a guided `control` vs. `treatment` comparison with paired git worktrees, so you can measure whether RepoCtx actually changes the outcome on a real task.

```bash
repoctx experiment
```

The wizard:

- collects one shared prompt for both lanes
- creates two clean worktrees from the same base commit under `.worktrees/`
- hands you off to the `control` worktree first
- on re-run, records control costs, prepares the `treatment` worktree, writes `.cursor/mcp.json` there so RepoCtx is enabled in that lane only
- after the treatment run, prints the summary

Fast path if you already know the prompt:

```bash
repoctx experiment "refactor the auth middleware to support OAuth"
```

The wizard asks for **cost before** and **cost after** for each lane so RepoCtx can compute the delta.

**Control-lane MCP suppression:** during the control lane, RepoCtx **stubs** MCP tool results (empty context + short message) so agents don't get RepoCtx retrieval even when RepoCtx is registered in Cursor's global `mcp.json`. Timing is controlled by `~/.repoctx/config.json`:

| Key | Default | Meaning |
|-----|---------|---------|
| `experiment_mcp_suppress` | `true` | Set `false` to disable arming entirely |
| `experiment_mcp_idle_ttl_seconds` | `90` | Auto-clear suppression after this many seconds of CLI inactivity |
| `experiment_mcp_extend_seconds` | `600` | Each `repoctx` CLI run extends the deadline by this many seconds |

State lives at `~/.repoctx/telemetry/experiment-mcp-suppress.json`. Cleared when you record a lane, start the treatment handoff, or hit the idle deadline.

More detail: **[docs/experiment-mcp-suppression.md](docs/experiment-mcp-suppression.md)**.

**What the experiment measures:**

- manual before/after cost checkpoints from your agent UI
- git-derived change statistics from each isolated worktree
- optional completion + verification status

**Limitations:**

- doesn't measure external agent time automatically
- doesn't score quality automatically
- cost accuracy depends on the numbers you enter

### Legacy and migration notes

**Legacy MCP entry point.** The original v1 tool `get_task_context(task)` is still exposed and works as a basic context-pack call. It's superseded by `bundle(task)`, which returns the same context plus authority records, constraints, edit scope, validation plan, and risk notes. New integrations should call `bundle`.

**Embedding index schema.** The on-disk index format changed in 1.0.0 (`schema_version: 2`). Indexes built before 1.0.0 raise `IndexSchemaMismatch` on load — delete `.repoctx/embeddings/` and re-run `repoctx index` once after upgrading.

**Index location moved (1.5.0).** The embedding index moved from the in-tree `<repo>/.repoctx/embeddings` to the shared `<git-common-dir>/repoctx/embeddings`, so all worktrees of a repo share one index. Migration is automatic on the next read or `repoctx index` (the old dir is moved up, or read in place if the move fails). No action needed; you can delete a leftover in-tree `.repoctx/embeddings` once the shared one exists.

**Tradeoffs & decisions (1.5.0).**

- *Shared-index location.* Keying on the git common dir (vs. a hashed global cache) means zero GC surface, automatic filesystem sharing across worktrees, and the index never appears as dirty/untracked. The cost: if you move `.git`, the index is "lost" — but it's a derived cache, so just rebuild.
- *Fetch cadence.* `git fetch origin main` is TTL-gated (default 30 min) and the attempt time is recorded even on failure, so an offline repo isn't hammered. The re-embed of an advanced base is incremental and capped (`base_refresh_max_files`, default 200); past the cap, or with `REPOCTX_BASE_REFRESH_ON_READ=0`, you get a warning instead of a synchronous re-embed.
- *Overlay scope.* Only *your* worktree's delta is overlaid. Sibling worktrees' uncommitted bytes are deliberately never indexed (dirty, half-finished, multi-tool) — the advisory lane uses committed branch tips instead.
- *Advisory authority.* The advisory lane is a separate index and a separate response key; it is never merged into authoritative results, preserving the bundle's trust model (authoritative = live origin/main + your overlay only).

**Nudge block format.** The anchored `<!-- repoctx-nudge -->` block in `CLAUDE.md` / `AGENTS.md` evolved between releases. Older anchored blocks are rewritten in place on the next `install` / `refresh` without touching surrounding doc content — no manual cleanup needed.

---

## Development

```bash
git clone https://github.com/gald33/repoctx.git
cd repoctx
python3 -m pip install -e ".[dev,embeddings]"
python3 -m pytest -q
```

Agent guidance for working in this repo: **[AGENTS.md](AGENTS.md)**.

## License

MIT
