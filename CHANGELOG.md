# Changelog

All notable changes to `repoctx` are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[SemVer](https://semver.org/).

## [Unreleased]

### Fixed — multi-line `from X import (...)` clauses lost their submodule edges

1.10.0 resolved imported names to submodules but was deliberately line-scoped,
so a clause continued across lines contributed nothing: `from pkg import (`
yielded an empty name list. Backslash continuations had the same gap.

`_complete_from_clause` now follows the continuation with an **explicit bounded
scan** (parenthesis depth / trailing backslash, capped at 50 lines) rather than
a wider regex — a `\s`-based class spanning lines is what caused the 1.9.0
crash, so the continuation is tracked in code where it can be reasoned about
and tested. Comments are stripped per line, and the scan stops at the closing
paren so it can't run on into unrelated code.

The scanner's import harvesting had to match: `import_source` (used for files
past the 16 KB truncation cap) was built by filtering to lines starting with
`import`/`from`, which kept `from pkg import (` and dropped the indented names
beneath it. It now emits each statement with its continuation lines intact.

On repoctx itself this recovers a further **346 → 358 edges**.

## [1.10.0] — 2026-07-14

Two dependency-graph blind spots, both found by pointing repoctx at its own
code and not believing the answer. Neither ever raised an error — they just
returned a smaller graph than reality, so `detect_changes` understated blast
radius and bundles quietly omitted related files. On repoctx itself the fixes
recover **318 → 346 edges (~9%)**.

### Fixed — `from package import submodule` created no edge to the submodule

`PYTHON_FROM_RE` captured only the package path, so `from pkg import mod`
produced an edge to `pkg/__init__.py` and **none** to `pkg/mod.py` — the actual
dependency. The idiom is ubiquitous; repoctx uses it for
`from repoctx import reporting` in `telemetry.py`, `mcp_server.py`,
`commands/protocol_ops.py`, and `main.py`. Asked who depends on
`reporting.py`, repoctx answered "only `tests/test_reporting.py`."

The regex now also captures the imported-names clause, and each name is
resolved as a candidate submodule alongside the package. Names that aren't
modules (`from pkg import CONSTANT`) simply don't resolve, so no false edges
are invented; `as` aliases resolve the module, not the alias; relative forms
(`from . import mod`, `from .. import other`) work. Known limitation: a
parenthesized clause continued across lines yields only the first line's names
(the package edge is still added, so no worse than before).

### Fixed — imports below the content-truncation cap were invisible

`FileRecord.content` is capped at `max_file_bytes` (16 KB) and the graph read
imports from that truncated text, so in any larger module every import past the
cap was dropped — and large files are precisely the central hubs. In repoctx,
12 of 137 indexed `.py` files hit the cap, including `mcp_server.py`,
`embeddings.py`, `telemetry.py`, and `reporting.py`. `mcp_server.py` imports
`reporting` at lines 543/1014/1101, all past 16 KB, so that dependency did not
exist in the graph at all.

`FileRecord` now carries `import_source`: the import-bearing lines harvested
from the **untruncated** text, populated only when truncation actually dropped
something (Python only — the TS extractor matches across lines and can't be
line-filtered safely). The full text was already read before slicing, so this
costs no extra I/O and adds ~1 KB for a 16 KB-capped file.

### Fixed — CLI protocol ops recorded no telemetry

The v2 protocol ops recorded a `protocol_op` event only on the MCP surface.
`record_protocol_op` was called from `_run_op` in `mcp_server.py`
(`surface="mcp"`), but the CLI entrypoints in `commands/protocol_ops.py`
(`bundle`, `authority`, `scope`, `validate-plan`, `risk-report`, `refresh`,
`detect-changes`) never called it — so all CLI usage was invisible to the
reporting/ingest pipeline, CLI failures produced no dogfood tracebacks (the
1.8.0 dogfood lane only covered MCP), and the per-repo retrieval tuner added
in 1.4 saw only MCP-sourced events, biasing its data. Surfaced while verifying
the 1.9.0 autoflush fix: `repoctx scope "..."` under `REPOCTX_DOGFOOD=1`
produced zero telemetry events and zero uploads.

- A shared `_run_op` helper in `commands/protocol_ops.py` now wraps every CLI
  protocol op, emitting `record_protocol_op(surface="cli", ...)` on success and
  failure. It mirrors the MCP recorder, including the dogfood-only
  message/traceback capture via `reporting.capture_exc_detail(exc)` on the
  failure path. Op names use the MCP-shared underscore form
  (`validate_plan`, `risk_report`, `detect_changes`) so CLI and MCP events
  aggregate together. Telemetry failures never mask the op result.

## [1.9.0] — 2026-07-14

Four fixes, all found by the dogfood lane on its first day. The reporting bug
is why the other three went unseen for six weeks.

### Fixed — queued reports never uploaded from the MCP server

`DEFAULT_FLUSH_BATCH_BYTES` was defined and documented as an "opportunistic
flush threshold" but **never referenced**, so the only automatic upload was the
`atexit` hook. The MCP server is a long-lived process terminated with SIGTERM
at session end, and Python does not run `atexit` handlers on SIGTERM — so every
event it queued sat on disk forever. Observed in the wild: **215 events
accumulated over six weeks with zero uploads**, silently capped by the 1 MB
queue limit.

- **`maybe_flush_async()`** now drains from the enqueue path, triggering when
  the queue reaches `DEFAULT_FLUSH_BATCH_BYTES` (64 KB) **or** when the last
  flush was more than `DEFAULT_FLUSH_INTERVAL_SECONDS` (5 min) ago — size alone
  strands light users for weeks. Runs on a daemon thread with a single-in-flight
  guard, so a burst of ops never spawns a thread each and never blocks a tool
  call on network I/O. `atexit` is kept as a backstop.
- **`REPOCTX_REPORTING_AUTOFLUSH=off`** disables it, for anyone who wants
  uploads only on an explicit `repoctx reporting flush`.
- **Test isolation.** `DEFAULT_ENDPOINT` is the real production URL, so a
  maintainer running the suite with `REPOCTX_DOGFOOD=1` exported would have
  POSTed synthetic events to production. A suite-wide `conftest.py` fixture now
  hard-disables reporting and autoflush.

### Fixed — one `import` line could take down every protocol op on a repo

`PYTHON_IMPORT_RE` used `[.\w\s,]+`, whose `\s` matches newlines. A single
`import os` therefore swallowed every following line until a character outside
the class, capturing whole `from x import a, b,` blocks in one match. Two
consequences:

- **Crash.** When the captured run ended on a trailing comma, the final split
  segment was empty and `item.strip().split()[0]` raised `IndexError` — from
  inside `build_dependency_graph`, which every protocol op calls. One such file
  anywhere in the repo meant `bundle`, `scope`, `validate_plan`, `risk_report`,
  `refresh`, and `detect_changes` all failed, 100% of the time.
- **False edges (silent).** Symbols from the absorbed `from ... import a, b`
  lines were looked up as module names, so a symbol sharing a name with a real
  module (`from .utils import config` → `config.py`) created a bogus dependency
  edge, quietly degrading retrieval on *every* repo — not just crashing ones.

The regex is now line-scoped (`[ \t]` instead of `\s`), and empty segments are
skipped rather than indexed into. `import x as y` still resolves the module.

### Fixed — virtualenvs with non-standard names were indexed

`IGNORED_DIRS` knows `venv` and `.venv`, so a virtualenv named anything else
(`myenv`, `env311`, …) had its entire vendored `site-packages` tree pulled into
the index — third-party code competing with the user's own for retrieval slots,
and the source of the crashing import above. Detection is now **structural**:
any directory containing a `pyvenv.cfg` (PEP 405's venv marker, name-independent)
is pruned during the walk, and `site-packages` is added to `IGNORED_DIRS` for
vendored trees without a marker. On the repo that surfaced this, the index drops
from 1537 files to 1017.

## [1.8.0] — 2026-07-14

### Added — dogfood failure reporting (opt-in, full error detail)

The default reporting lane uploads error *classes* only — enough to count
failures, useless for debugging them. A new maintainer-only **dogfood** mode
(`REPOCTX_DOGFOOD=1`) uploads the exception message and traceback alongside,
so agent-hit repoctx failures arrive actionable.

- **Client.** `REPOCTX_DOGFOOD=1` implies reporting-on (still overridable by
  `REPOCTX_REPORTING=off`) and exempts `error_message`/`traceback` from the
  upload redaction, tagging the payload `dogfood: true`. `repoctx.reporting.
  capture_exc_detail()` captures a truncated message + traceback **only** in
  dogfood mode — off dogfood nothing but the error class is ever recorded,
  even locally. Wired into the MCP protocol-op path (`bundle`, `authority`,
  `scope`, `validate_plan`, `risk_report`, …). Surfaced in `reporting status`.
- **Ingest Worker.** Accepts `error_message`/`traceback` **only** when
  `dogfood: true`; without the flag they're rejected like any other forbidden
  key, so public/canary users are unaffected. New nullable columns via
  `server/migrations/0002_dogfood.sql` (needs `wrangler d1 migrations apply`
  + `wrangler deploy`).
- Privacy: paths, queries, code, remotes, and host/user are still stripped
  even in dogfood — only the message and traceback ride along (a traceback may
  embed install paths, acceptable for a maintainer debugging their own tool).

## [1.7.0] — 2026-07-13

### Added — semantic retrieval provisions itself in remote sessions (zero setup)

Connecting was only half the cloud story: without a per-environment setup
script, remote sessions ran lexical-only forever — the `[embeddings]` extra,
the embedding model, and the index never existed in the fresh container. Now
the MCP server provisions all three itself, in the background, while already
serving:

- **`repoctx/autoprovision.py`.** In a remote container
  (`CLAUDE_CODE_REMOTE=true`; `REPOCTX_AUTO_EMBEDDINGS=1` forces on anywhere,
  `=0` is the kill switch) the first tool call spawns a daemon thread that
  installs `repoctx-mcp[embeddings]` into the *running* interpreter's
  environment (`uv pip --python <this interpreter>` when uv is available —
  works inside uv ephemeral envs, immune to PEP 668 — else `pip`; torch
  resolves from the CPU wheel index), then builds/refreshes the origin/main
  index. Single-flighted in-process, journaled cross-process to
  `<state>/autoprovision.json` with a staleness window so a crashed run never
  blocks retries.
- **Mid-session activation.** `embeddings.refresh_embeddings_availability()`
  re-probes the optional deps after the runtime install and flips the
  import-time `HAS_EMBEDDINGS` / `vector_index.HAS_NUMPY` globals in place, so
  the very next tool call serves semantic results — no server restart. (Every
  consumer already reads these flags per call, not at module import.)
- **Consent stays honest.** A recorded `"declined"` index consent always stops
  provisioning. Otherwise, in remote containers consent is recorded as
  granted (with an `autoprovision`-surface telemetry event) before the build —
  the one-shot consent prompt was designed for local machines, where the
  download/disk cost lands on the user's own hardware.
- **Status is loud, not silent.** While provisioning runs, the
  `no_index`/`deps_missing` warning on bundles and `semantic_search` says
  semantic retrieval "is being provisioned automatically … no action needed";
  a failed run surfaces the error and the manual fix.
- **`repoctx autoprovision` CLI** runs the same sequence synchronously — the
  one-liner for cloud environment setup scripts (the pre-warmed fast path,
  since a cold in-session provision costs a few minutes once per container
  cache).

### Fixed — committed MCP configs were machine-pinned, so cloud sessions (and teammates) could never connect

`repoctx install` wrote the installing machine's absolute interpreter path and
`--repo <absolute repo path>` into `.mcp.json` / `.cursor/mcp.json` /
`.codex/mcp.json`. Those files are committed and travel with the repo — to
cloud containers (Claude Code on the web, Codex cloud) and teammates' machines
where neither path exists, so the server process could never spawn and every
session showed "failed to connect". Relying on a SessionStart hook to
pip-install first doesn't fix it: hosts don't guarantee hooks complete before
MCP servers launch.

- **The generated config is now portable and self-bootstrapping.** On POSIX it
  is a small launcher that tries, in order: interpreters that already have the
  server (the install-time interpreter, then `python3`/`python` from PATH —
  each probed for existence and for the `mcp` dependency, not just the
  package), then `uv run --no-project --with repoctx-mcp` (cached ephemeral
  env, preinstalled in Claude Code cloud images, immune to PEP 668), then a
  quiet `pip install` last resort with stdout redirected to stderr so MCP
  stdio framing stays clean. Windows keeps the pinned single-command form.
- **No more `--repo` pin.** Hosts launch project-scoped servers with cwd at
  the project root, which repo-root resolution already prefers as a live
  signal; a committed absolute path only re-broke portability.
- **Hook commands are portable too.** `.claude/settings.json` hooks now fall
  back from the pinned interpreter to `python3` and end in `|| true`, so a
  machine without repoctx no-ops silently instead of erroring on every
  prompt/edit/stop.
- **Auto-upgrade.** Re-running `repoctx install` rewrites a legacy pinned
  entry in place (idempotent afterwards). Existing repos need one re-install +
  commit.
- **New console script `repoctx-mcp`** (→ `repoctx.mcp_server:main`), so
  `uvx repoctx-mcp` starts the server straight from PyPI — a zero-install
  one-liner for manual MCP configs.
- README: the "Cloud sessions" section now separates the connection layer
  (zero-setup, self-bootstrapping) from the semantic layer (embeddings + index
  via the environment setup script).

### Fixed — MCP `initialize` handshake timed out during the cold embedding load

On a cold CPU host, loading the Qwen3-Embedding-0.6B weights can take >60s.
That load ran synchronously inside `create_server()` *before* the server
started serving, so the MCP `initialize` handshake couldn't be answered until
it finished — exceeding the client's ~60s per-request timeout. The connection
then failed with `MCP error -32001: Request timed out` and no tools ever
registered (reproduced reliably in Claude Code cloud sessions).

- **Embedding warm-up is now off the startup path.** `create_server()` warms
  the retriever in a background daemon thread (`repoctx-embed-warm`), so
  `initialize` / `tools/list` are answered immediately. The first tool call
  drives the load to completion via a thread-safe, load-at-most-once helper
  (a lock + completion event single-flights the model load), so a stampede of
  concurrent first calls — and the warm-up thread — collapse to one load.
- **Escape hatch.** `REPOCTX_EAGER_EMBEDDINGS=1` restores the legacy blocking
  preload (load on the startup thread before the server serves).
- No behavior change for normal users; embedding-ranked results are unchanged.

### Changed — cached embedding model loads with zero network

Even after the warm moved off the startup path, the model load still made ~15
Hugging Face metadata round-trips (`HEAD`/`GET` to huggingface.co) on every
start — a cached model included. Through an egress proxy those add ~10s and, on
a cold cloud runner, are enough to push the background warm (or a legacy
`REPOCTX_EAGER_EMBEDDINGS` preload) back past the ~60s `initialize` ceiling.

- **Offline-when-cached.** When the model is already in the local Hugging Face
  cache, `EmbeddingModel` now loads it with `local_files_only=True`, so the warm
  does no network at all (measured cold `initialize`: ~17s online → ~9s
  offline). A genuine first-run download still works: the offline path is only
  taken when the model is cached, and it falls back to a network-capable load if
  the cache turns out incomplete.
- **Override.** `REPOCTX_EMBEDDINGS_OFFLINE=1` forces offline everywhere; `=0`
  disables the optimization (always allow network). Unset = auto (offline iff
  cached).
- Removes the need for downstream repos to wrap the server in an
  `HF_HUB_OFFLINE` launcher just to connect reliably in cloud.

### Fixed — cross-repo identity bleed in the long-lived MCP server

A single MCP server process shared across multiple repos (the common Claude
Desktop / global-config setup) could silently answer for the *wrong* repo,
surfacing another project's contracts, tests, and architecture docs in
`bundle` / `validate_plan`. Root cause was identity caching in
`repoctx/mcp_server.py`, not the on-disk embedding index — so an index
rebuild never fixed it.

- **Live signal now beats a stale session memo.** When a call omits
  `repo_root`, resolution prefers a live workspace signal (host env, a real
  cwd/`$PWD`, or an explicit `--repo`) over the memoized root, so a
  mid-session repo switch is honored instead of serving the previously
  resolved repo. The memo remains the fallback for launchd hosts (cwd `/`,
  no workspace env) it was introduced for; a live signal that doesn't resolve
  to a repo falls back to the memo rather than erroring.
- **Embedding retriever reloads on a repo switch.** The cached retriever
  wraps a per-repo index; it's now keyed to the repo it was loaded for and
  reloaded when the resolved root changes, so a new repo's task can never be
  scored against a previous repo's vectors.
- Why `risk_report` looked "clean" while `validate_plan` didn't: both build
  the same bundle, but `risk_report` only emits notes when your
  `changed_files` intersect the (foreign) constraints/protected paths, while
  `validate_plan` echoes the bundle's `validation_plan.tests` unconditionally.

## [1.6.0] — 2026-06-19

### Added — cloud-session setup (Claude Code on the web, Codex)

Make RepoCtx usable in ephemeral cloud sessions, where the container is cloned
fresh each time and lacks the package, the embedding model, and the index.

- **`scripts/cloud-setup.sh`** — shared, idempotent setup: `pip install -e
  ".[embeddings]"` (skipped when already importable) then `python3 -m repoctx
  index --refresh` (full build + Qwen3 model download on the first run; only the
  `origin/main` delta thereafter). The container caches its filesystem after the
  first run, so the cold start pays the full install + build (a couple of
  minutes) while warm sessions skip the install and do a near-no-op refresh — a
  few seconds.
- **Claude Code on the web** — a `SessionStart` hook
  (`.claude/hooks/session-start.sh`, registered in `.claude/settings.json`) runs
  it automatically, gated to remote sessions (`$CLAUDE_CODE_REMOTE`) so it's a
  no-op locally. Synchronous by default; switch to async via a one-line change.
  MCP server registered in `.mcp.json`.
- **Codex** — MCP server registered in `.codex/config.toml`; point the Codex
  cloud environment's setup script at `bash scripts/cloud-setup.sh`.
- Needs egress to PyPI + huggingface.co; without it the build can't run and
  retrieval degrades to lexical-only (loudly).

### Added — index-build timing telemetry

Every embedding-index build now records an `index_build` telemetry event with a
phase breakdown, so the build cost we kept *estimating* (notably "is a slow
build the one-time model load/download, or the corpus embed that scales with
repo size?") is actually measured.

- **What's captured.** `build_index` fills an optional `metrics_out` dict with
  `model_load_ms` vs `embed_ms` vs `scan_ms`, `total_ms`, chunk/file counts,
  `embedded_chunk_count` (how much an incremental build actually re-embedded),
  and the resolved `device`/`dtype`/`model_name`. The CLI (`repoctx index` /
  `rebuild`) and the MCP `index` tool both emit the event; the CLI also prints
  a one-line breakdown after each build.
- **Surfaced in `repoctx stats`.** The total lands in the per-op latency table
  automatically (it keys off `duration_ms`); a dedicated **Index builds**
  section adds the model-load/embed/scan split and per-build corpus size.
- **Privacy.** Every field is a count, timing, or low-cardinality enum
  (`model_name` is a constant model id, `device` is `cpu`/`cuda`/`mps`) — no
  paths or content — so it rides the existing redacted reporting path
  unchanged when reporting is enabled.

### Added — anonymous reporting + canary release channel

Optional opt-in upload layer (`repoctx.reporting`) on top of the existing
local telemetry. Stable installs default OFF with no prompts ever; canary
installs default ON with a one-time stderr disclosure. Designed so a stable
install that never opts in produces zero files on disk.

- **Privacy contract.** Uploaded payloads carry counts, timings, and error
  *classes* only — never paths, queries, code, error messages, git remote
  URLs, hostnames, or anything correlatable across users. Repo identity is
  `sha256(install_id || first_commit_sha)` so it's stable per (install,
  repo) but not joinable across users. Server-side forbidden-key
  enforcement provides defense in depth.

- **Surface.** CLI `repoctx reporting {status,on,off,show,flush}`; MCP
  `reporting` tool with the same actions so an agent can inspect or flip
  the flag on the user's behalf; `REPOCTX_REPORTING=off` env kill switch.

- **Canary channel.** Pre-release wheels published via
  `pip install --pre repoctx-mcp`. `repoctx/_build_channel.py` carries the
  baked-in `CHANNEL` and `BUILD_ID`; the release pipeline rewrites it for
  canary builds. No auto-update — users upgrade when they choose.

- **CI integration.** Existing OIDC-based PyPI workflow extended with a
  `workflow_dispatch` channel input; canary path runs
  `scripts/release.py --channel canary --prepare-only` before
  `python -m build`. Trigger with
  `gh workflow run publish-pypi.yml -f channel=canary`.

- **Ingest endpoint.** Cloudflare Worker + D1 at
  `https://repoctx-reports.repoctx.workers.dev` (`server/`). Worker rejects
  events containing any forbidden top-level key independently from the
  client.

## [1.5.1] — 2026-05-21

### Added

- `repoctx --version` flag — prints the installed version and exits. Previously
  argparse rejected it with "unrecognized arguments". Resolves via
  `importlib.metadata`, with a safe `unknown` fallback when run from an
  uninstalled source tree.

## [1.5.0] — 2026-05-21

### Added — worktree-aware index pinned to live origin/main

Fixes silent retrieval degradation when repoctx is used from a git worktree:
the index lived in the working tree, so a worktree never found the index built
in the main checkout and quietly fell back to lexical ranking.

- **Index keyed by repo identity, shared across worktrees.** Stored at
  `<git-common-dir>/repoctx/embeddings` (resolved via
  `git rev-parse --git-common-dir`) instead of `<cwd>/.repoctx/embeddings`, so
  every worktree and the main checkout share one index. Lives under `.git/`, so
  it's never tracked or seen as dirty. `recent_repos.json` is deduped by
  identity so worktrees collapse to one entry. New `repoctx/index_location.py`,
  `git_state.git_common_dir()`.

- **Fail loud instead of silent lexical fallback.** `semantic_search` now
  returns an envelope `{status, message, results}` (`no_index` ≠ "no matches");
  `bundle` carries top-level `warnings[]` and a `retrieval` block
  (`ranker`/`index_status`/`index_location`). New
  `embeddings.load_retriever_status()` / `probe_index_status()`.

- **Authoritative index pinned to `origin/main`, read from git objects.** New
  `repoctx/git_tree.py` reads the tree via `ls-tree`/`cat-file` (no checkout),
  so landed work is retrievable even from a branch that predates it. `git fetch`
  is TTL-gated; `repoctx index` defaults to `--source origin-main` (`--source
  worktree` opts out); `repoctx index --refresh` re-embeds the delta. Read-path
  auto-refresh is TTL-gated and capped (`REPOCTX_BASE_REFRESH_ON_READ=0` for
  warn-only).

- **Worktree delta overlaid at query time.** Commits ahead of origin/main plus
  uncommitted edits are embedded on the fly and layered over the base, so
  in-progress work is retrievable as if rebased. New `repoctx/overlay.py`
  (`REPOCTX_OVERLAY_WORKTREE=0` to disable).

- **Opt-in advisory lane** (`repoctx/advisory.py`) over committed branches ahead
  of origin/main, for "is this already being built elsewhere?". Separate index,
  separate response key, provenance-tagged (branch / commits-ahead /
  last-commit-date / merge-status), never mixed into authoritative results. New
  `advisory_search` MCP tool, `advisory-index` / `advisory-search` CLI, and a
  `bundle(include_advisory=True)` flag.

- **Automatic migration** of pre-existing in-tree `.repoctx/embeddings` to the
  shared location on the next read or `repoctx index`.

## [1.4.0] — 2026-05-14

### Added — per-repo retrieval tuning loop (feedback events + MAP-fit model)

Replaces the fixed `embedding_qualify_threshold = 0.3` with a learned
per-(kind, subkind) threshold fit from observed agent behavior. Closes the
loop locally: collect feedback while you work → `repoctx eval` to inspect →
`repoctx tune` to fit → loader picks up the result on the next bundle.

- **Per-repo config** at `<repo>/.repoctx/config.json` plus
  `REPOCTX_QUALIFY_THRESHOLD_<KIND>` / `REPOCTX_LEXICAL_TIEBREAK_<KIND>` env
  vars. Hierarchical threshold lookup (`code/handler` → `code` → `_default`)
  so subkind-specific tuning is "free" once a cell collects labels and
  silently inactive otherwise. Opt-out with `"feedback_enabled": false`.

- **Feedback log** at `<repo>/.repoctx/feedback-events.jsonl`. Three signal
  sources, each tagged with provenance so the tuner can weight them:
  - **PostToolUse hook** (`repoctx hook tool-use`) — silent
    `Read|Edit|Write|MultiEdit` handler. Attributes to the most recent
    matching bundle within a 30-min / 200-tool-use window. Auto-wired by
    `repoctx install` as a second PostToolUse entry alongside the existing
    embedding-upkeep hook.
  - **`mark_used` MCP tool** — graded relevance (`informed_edit` /
    `informed_context` / `noise`). The LLM judge is the only signal that
    captures "I read A and it shaped my edit of B" — structurally invisible
    to hooks and git-diff. Suggested via the bundle's
    `before_finalize_checklist`.
  - **Git-diff reaper** (`repoctx reap`, plus auto-runs on `Stop` and at the
    next `bundle`) — universal fallback for IDEs without PostToolUse hooks.
    Enumerates `git worktree list --porcelain`, idempotent via per-bundle
    dedup.

- **`repoctx eval`** — joins `tool_use` / `self_report` / `git_edit` events
  back to `bundle_emitted` by `bundle_id`. Reports per-(kind, subkind)
  precision-ish (fraction of bundle used), recall-ish (fraction of touched
  paths bundled), and explicit noise rate from `mark_used`.

- **`repoctx tune`** — 1-D Bayesian MAP grid search per cell. Provenance
  weights: hook+Edit=1.0, git=0.8, self_report `informed_edit`=0.9 /
  `informed_context`=0.7 / `noise`=1.0, hook-Read-only=0.3. 30-day half-life
  decay. Strong Gaussian prior (σ=0.07) on the configured default — works
  at ~10–50 labels per cell without overfitting. Two-pass hierarchical fit
  so subkinds shrink toward the parent kind's evidence. `--dry-run` (default)
  prints proposed deltas; `--apply` writes to the `learned` block in
  `.repoctx/config.json`.

- **Exploration budget** (`exploration_epsilon = 0.05`) — retriever
  occasionally surfaces 1–2 sub-threshold near-miss embedding candidates
  per bundle so the tuner can observe what the current threshold filters
  out. Without this the loop is structurally blind to "lower the threshold"
  signals.

- **Subkind classifier** — deterministic, no ML. Path patterns plus light
  import-sniffing (`fastapi`/`flask`/`pydantic`/`argparse`/`GENERATED`
  markers). `code: handler/model/cli/util/scaffold/generated/other`,
  `doc: agent_contract/architecture/readme/other`,
  `config: build/ci/lint/other`. `test` stays flat (geometry inside tests
  is less differentiated).

- **Bundle schema bump** to `repoctx-bundle/2`: adds a stable `id` field
  (uuid hex16) for feedback-event attribution. Backwards-compatible field
  addition — existing consumers that read documented fields keep working.

### Fixed — installer pinned to `sys.executable`

Hooks and `.mcp.json` previously wrote bare `repoctx` / `python` commands,
which silently no-op'd on venv / pipx / uv installs because Claude Code
launches hooks and the MCP server via the user's shell, whose `PATH` may
not include the install prefix. Now writes the absolute interpreter path
that ran `repoctx install`, in all three harnesses (Claude Code, Cursor,
Codex).

`_ensure_hook_entry` and the MCP config writers detect stale bare-command
entries from prior installs and **upgrade them in place** on the next
`repoctx install`. Users who already installed an older version just
re-run install once to self-heal.

### Notes

- 92 new tests across `feedback_log`, `mark_used`, hook handler, reaper,
  eval, tune (incl. provenance weighting + time decay), exploration budget,
  subkind classifier, hierarchical threshold lookup, and end-to-end tune
  fallback chain. 0 regressions on the rest of the suite.
- Optimization target is **alignment, not truth**: the tuner fits "what the
  LLM finds useful given what we ship", not "what was objectively correct".
  Outcome signals (PR merge / revert / CI) are deliberately out of scope —
  documented in `tune.py`'s module docstring along with the other honest
  caveats (exposure bias, self-attribution noise, thin per-repo data).

## [1.3.0] — 2026-05-13

### Documentation

- **README rewrite around the ground-truth-bundle framing.** Restructured
  for the actual reader path (problem → bundle → install → setup → tools →
  details), consolidated the three near-identical editor setup blocks, and
  promoted the Ground-Truth Bundle to the headline. Added a top-of-file
  callout pointing AI agents at `AGENTS.md`. Moved controlled experiment
  mode and v1/migration notes into an appendix so the main body reads
  forward-looking. Trimmed from 614 to ~340 lines without dropping
  technical content.

### Added — task-entry / task-exit nudges via Claude Code hooks

Telemetry from active consumer repos showed that even with the v1 anchored
nudge block in place, `bundle()` and `validate_plan()` were still not
getting called on non-trivial commits — the block is documentation, not
behavior. This release adds harness-level hooks that make those calls
fire, and tightens the directive so the agent reads it as a requirement
instead of a suggestion.

- **New CLI subcommand `repoctx hook`** with two sub-actions, both reading
  Claude Code hook JSON from stdin and always exiting 0 so they can never
  block the user's flow:
  - `repoctx hook prompt-nudge` — `UserPromptSubmit` handler. Substantive
    prompts (length > 40 OR matches
    `\b(implement|refactor|fix|add|build|rewrite|migrate|integrate|design)\b`)
    print a one-line reminder to call `mcp__repoctx__bundle`. Trivial
    prompts produce no output.
  - `repoctx hook stop-check` — `Stop` handler. Reads the session
    transcript, counts `Edit|Write|MultiEdit` tool uses and
    `mcp__repoctx__validate_plan` calls in the current turn, and prints a
    stderr reminder if edits happened without `validate_plan`. Honors
    `stop_hook_active` to prevent loops.

- **`repoctx install` wires both hooks automatically** alongside the
  existing `PostToolUse` `repoctx update --from-claude-hook` entry. All
  three entries are JSON-merged into `.claude/settings.json`; unrelated
  user-authored hooks are preserved, and matching is by command prefix so
  re-running install never duplicates entries.

- **Anchored nudge block bumped v1 → v2** with stronger phrasing (`**must
  call**`) and an inline definition of "non-trivial" (touches >1 file OR
  introduces new behavior OR adds/removes a public API). On the next
  `install` / `refresh`, existing v1 blocks are rewritten in place — the
  surrounding document is preserved and the upgrade is idempotent thereafter.

- **Dev-only `REPOCTX_LEARN=1`** appends `If you decide to skip this,
  briefly state the reason.` to both reminders so adoption-tuning weeks
  can capture skip rationale. Off by default — regular sessions don't pay
  the token cost on every non-trivial turn.

Existing installs upgrade on the next `repoctx install`. The
`InstallResult` shape is unchanged (the new hook entries are reflected in
the existing `settings_changed` flag); `claude_md_action` may now report
`nudge_inserted` against a v1-marked file when the block was rewritten in
place to v2.

## [1.2.0] — 2026-05-07

### Added — pointer-aware repoctx-nudge in CLAUDE.md / AGENTS.md

Claude Code auto-loads `CLAUDE.md` but not `AGENTS.md`, so even a
thorough repoctx section in `AGENTS.md` was invisible to the harness on
real projects (`mcp__repoctx__stats` showed near-zero bundle calls
despite well-written guidance). `install` and `refresh` now place a
short anchored block — marker `<!-- repoctx-nudge:v1 -->` — where
whichever tool is reading will see it.

- `install_claude_code` and `op_refresh` classify each markdown file as
  `absent`, `pointer`, or `content`. A *pointer* is either a file we
  created (`<!-- repoctx-pointer:v1 -->` marker) or a hand-written file
  ≤500 bytes whose only substantive line is a `@OTHER.md` import.
- Placement matrix: CLAUDE absent + AGENTS content → create CLAUDE.md
  as `@AGENTS.md` pointer, nudge in AGENTS.md. CLAUDE pointer + AGENTS
  content → nudge in AGENTS.md only. Both content → nudge in **both**
  so single-file readers don't miss it. CLAUDE content + AGENTS
  pointer/absent → nudge in CLAUDE.md only.
- Idempotent and self-healing: deleting the block and re-running
  `install` or `refresh` re-inserts it byte-identically.
- Opt-out via `--no-claude-md-nudge` (CLI flag on `install`,
  `install-claude-code`, `refresh`) and `REPOCTX_NO_CLAUDE_MD_NUDGE=1`.
- `InstallResult` gained `claude_md_action`
  (`pointer_created` | `nudge_inserted` | `no_op` | `skipped`) and
  `agents_md_nudge_changed`. `op_refresh` now returns
  `claude_md_nudge: {claude_md, claude_md_action, agents_md,
  agents_md_action, any_inserted}`.

Existing repos already on 1.1.x see no change until the next
`install` / `refresh` runs against this version.

## [1.1.1] — 2026-05-01

### Fixed — `bundle()` / `get_task_context()` now use the embedding index

Prior to 1.1.1, `bundle()` ranked `relevant_code` by lexical token
overlap even when an embedding index was present, and `get_task_context`
treated the cosine score as a small additive boost that the lexical
heuristic almost always swamped (a 3-token name overlap = 18.0 already
beat a perfect cosine ×12.0 weight). Building the index improved
`semantic_search()` only — the recommended task-shaped entry points
were strictly worse than the raw similarity tool.

- `op_bundle` now loads the persisted retriever (`try_load_retriever`)
  and passes `query_scores(task)` into `build_bundle`. The MCP `bundle`
  tool inherits this — no client-side change needed.
- When embedding scores are present, cosine is the primary signal and
  the lexical heuristic is squashed via `tanh(heuristic / 12)` ×
  `lexical_tiebreak_weight` (default 0.05) so it can only break ties
  between near-equal cosines.
- `RankedPath.reason` now reports the contributing signal:
  `"Semantic similarity 0.66; tokens: telegram, debug"`,
  `"Semantic similarity 0.66"`, or the existing
  `"Matches task tokens: …"` fallback.
- `bundle.metrics["ranker"]` is now `"embeddings"` or `"lexical"` so
  callers can verify which path ran.
- `.uv-cache` added to `IGNORED_DIRS`. Existing indexes still contain
  these chunks until the next `repoctx index` rebuild, but the new
  primary-cosine ranker deprioritizes them on content anyway.
- New config knob: `RepoCtxConfig.lexical_tiebreak_weight: float = 0.05`.

No rebuild required: the fix takes effect immediately against any
schema-compatible existing index.

## [1.1.0] — 2026-04-30

This release pairs the new live embedding-update queue with the
incremental rebuild work that landed on `main` between 1.0.3 and 1.1.0:
together they cover both ends of "keep the index current" — per-edit
upkeep via the queue, bulk catch-up via `index --incremental`.

### Added — Debounced embedding-update queue
- `repoctx update <file>` no longer embeds synchronously. It appends to
  `.repoctx/embeddings/.pending` (JSONL, file-locked) and auto-flushes
  once the queue reaches `debounce_n` unique paths **or** the oldest
  entry is older than `debounce_max_age_seconds` — whichever hits first.
  Defaults: 10 paths / 300 s.
- Concurrent enqueueing is safe via `flock`; the queue is deduped by
  path on flush; partial-flush failures re-queue survivors; a flush
  killed mid-batch leaves a `.pending.flushing` sidecar that's recovered
  on the next call.
- New `repoctx update` flags: `--immediate` (bypass queue, embed now),
  `--flush` (drain the queue), `--status` (print depth + oldest age as
  JSON), `--from-claude-hook` (parse the Claude Code PostToolUse stdin
  JSON and queue the edited file).
- New `EmbeddingConfig` fields: `auto_flush` (default `True`),
  `debounce_n` (10), `debounce_max_age_seconds` (300), `queue_filename`
  (`.pending`).

### Added — Automated upkeep across harnesses
- **Claude Code PostToolUse hook**: `repoctx install` and
  `install-claude-code` now write `.claude/settings.json` with a hook
  that runs `repoctx update --from-claude-hook` after every `Edit |
  Write | MultiEdit`. Idempotent — existing hooks are preserved and the
  repoctx entry is detected on re-install.
- **Harness-agnostic instruction**: the `AGENTS.md` "Ground truth
  (repoctx)" section now includes an `### Embedding upkeep` blurb so
  agents on Cursor / Codex / any other AGENTS.md-driven harness have a
  written rule to call `repoctx update <path>` after every edit even
  when hooks aren't available. The blurb also tells agents to prefer
  `repoctx index --incremental` for bulk catch-up.

### Added — Read-side auto-flush
- `op_bundle` and `op_scope` call `maybe_flush_on_read` before building
  the bundle so retrieval never reads a stale index even if the writer
  forgot to flush. No-op when the queue is empty.

### Changed — Schema-mismatch UX
- The vector-index loader now distinguishes a clear "outdated format"
  error from generic load failures and surfaces a friendly migration
  message at WARNING level (no `--verbose` needed). The
  `IndexSchemaMismatch` text spells out both migration paths
  (`repoctx rebuild` for clean restart, `repoctx index --incremental`
  for diff-only re-embed) and reassures that no source data is lost.

### Added — `repoctx index --incremental` (carried forward from `main`)
- **`repoctx index --incremental`**. Opt-in flag that re-embeds only chunks
  whose `content_hash` differs from the existing on-disk index. Unchanged
  chunks reuse their persisted vectors; chunks with changed text are
  re-embedded; chunks (and files) that disappeared in the new scan are
  dropped. New `incremental: bool = False` parameter on
  `repoctx.embeddings.build_index` exposes the same behaviour to library
  callers. Default behaviour is unchanged (full rebuild) — promotion to
  default is deferred to a later minor release.
- **Compatibility guard**. `index_config.json` now records the
  `chunk_config` used to build the index (target/max/overlap/min tokens).
  Incremental rebuilds refuse to splice when the on-disk `model_name` or
  `chunk_config` differ from the current run, falling back to a full
  rebuild with a warning. Indices missing this metadata (built before this
  release) also trigger fallback. Old indices still load fine — the field
  is only consulted by the incremental path.
- **`semantic_search` MCP tool + `repoctx semantic-search` CLI**. Direct
  top-K cosine-similarity lookup over the per-chunk embedding index.
  Returns raw hits (`path`, `score`, `snippet`, `start_line`, `end_line`,
  `enclosing_symbol`) sorted by descending similarity, with optional
  `kind` filter (`code` / `doc` / `test` / `config`). Skips the heuristic
  blending, scope inference, and authority bundling that `bundle` /
  `get_task_context` / `scope` perform — for agents that want a primitive
  "what chunks look most like this string" lookup rather than a
  task-shaped bundle. Returns `[]` (with a log line) when no index is
  present, so the cold-start path never errors.

## [1.0.3] — 2026-04-27

Polish release. Cleaner indexing on repos with active Claude Code worktrees.

### Fixed
- **`.claude/` is now in `IGNORED_DIRS`**. Repos with active Claude Code
  worktrees were getting every file double-indexed (once at the canonical
  path, once under `.claude/worktrees/<branch>/...`), inflating the chunk
  count and adding noise to retrieval. The standalone `.worktrees/` entry
  remains for repos using that convention without `.claude/`.
- **`repoctx index` reports chunks vs. files honestly.** Output now reads
  `Indexed N chunks across M files` (was `Indexed N files`, where N was
  actually the chunk count). `index_config.json` reports `entry_count`
  (chunks) and `file_count` (distinct paths) as separate fields.

## [1.0.2] — 2026-04-27

Patch release. Big GPU memory reduction during indexing.

### Changed
- **Embedding indexing now uses fp16 + shorter sequences on accelerators.**
  Three additive optimizations cut peak GPU memory ~6-8× on Apple silicon
  (and CUDA) without affecting retrieval quality:
  - **fp16 weights & activations** when device is MPS or CUDA. Halves
    weight footprint and activation memory. CPU stays in fp32 (where fp16
    is slower in PyTorch). Override via `REPOCTX_EMBEDDING_DTYPE`
    (`fp16` / `fp32` / `auto`).
  - **`max_seq_length` default lowered to 256.** Attention activations
    scale as seq_len², so this alone is a ~4× cut. Most code chunks fit
    in 256 model tokens; longer chunks are truncated. Override via
    `REPOCTX_EMBEDDING_MAX_SEQ_LENGTH`.
  - **Super-batched encoding with cache eviction.** On MPS/CUDA, inputs
    are encoded in groups of `batch_size × 8` and `torch.{mps,cuda}.empty_cache()`
    is called between groups, bounding heap fragmentation across long
    indexing runs. CPU runs as a single call.

  Combined with the existing batch_size=8 clamp on MPS, peak Metal buffer
  drops from ~6 GB (1.0.0) to under 1 GB for typical chunks.

  CPU-fallback path additionally recasts back to fp32 since CPU fp16 is
  slower in PyTorch.

  `EmbeddingConfig` gains `dtype: str = "auto"` and `max_seq_length: int = 256`
  fields.

## [1.0.1] — 2026-04-27

Patch release. One-command first-time setup; reliable indexing on Apple silicon.

### Changed
- **`repoctx install` now auto-builds the embedding index** when the
  `[embeddings]` extras are importable, collapsing first-time setup to a
  single command. Use `--no-index` to opt out, or `--with-index` to require
  a build (errors if extras are missing). The install summary JSON gains an
  `installed.embedding_index` entry reporting `built` / `skipped` status.

### Fixed
- **Apple silicon MPS OOM during `repoctx index`** is now handled
  automatically. Chunk-aware embedding produces ~5× more rows per file than
  the previous whole-file approach, exposing a latent issue where
  `sentence-transformers` auto-selected MPS and tried to allocate a Metal
  buffer larger than physical memory.
  - **Auto-clamp**: when the resolved device is MPS, `batch_size` is
    capped at 8 (overridable via `REPOCTX_EMBEDDING_BATCH_SIZE`), reducing
    Metal allocator pressure substantially.
  - **Auto-fallback**: when encoding raises a `RuntimeError` (typical for
    catchable OOM), the model is moved to CPU and the encode is retried
    transparently. The query path falls back the same way.
  - **Manual override** for the rare uncatchable C++ assert path:
    `REPOCTX_EMBEDDING_DEVICE=cpu repoctx index`.

  `EmbeddingConfig` gains explicit `device` and `batch_size` fields
  (defaults: auto-detect / 16) for users who want to pin behavior.

## [1.0.0] — 2026-04-27

First stable release. Embedding retrieval is now chunk-aware.

### Changed
- **Embeddings now operate on chunks, not whole files.** `build_index` runs a
  symbol-aware sliding-window chunker over each file: code splits prefer
  top-level symbol boundaries (functions, classes, methods) over blank lines
  over single lines; prose splits prefer paragraph boundaries over sentence
  ends. This removes the 8000-char file truncation and lets long files be
  retrieved by their late-file content. Default chunk size: 400 target /
  600 max tokens with 80-token overlap.
- **Vector index schema bumped to v2** (`schema_version: 2` in
  `index_config.json`). Old indexes raise `IndexSchemaMismatch` on load with
  a rebuild prompt. Delete `.repoctx/embeddings/` and re-run `refresh` after
  upgrading.
- **`VectorIndex.similarity_scores` now max-pools per path** so multi-chunk
  files collapse to one score (their best-matching chunk). Per-chunk scores
  remain available via `similarity_scores_by_id`.

### Added
- **`repoctx.symbols`**: extracts function/class/method spans via Python's
  `ast` (stdlib) and tree-sitter for JS/TS/TSX/Go/Rust/Java. Go method
  receivers are captured and prefixed onto the method name; Rust impl-block
  methods qualify via the lexically enclosing impl span.
- **`repoctx.chunker`**: symbol-aware sliding-window chunker with overlap
  and a single line-based algorithm whose split-priority hierarchy adapts
  to code vs. prose.
- **`VectorIndex.delete_by_path` / `add_entries`** for chunk-level
  incremental updates; `update_file_in_index` now replaces all chunks for
  the changed file in one bulk operation.

### Dependencies
- `[embeddings]` extra adds `tree-sitter>=0.23` and
  `tree-sitter-language-pack>=0.7`. The base install is unchanged.

## [0.7.0] — 2026-04-27

### Added
- **`stats` CLI + MCP tool**: aggregates the telemetry already written to
  `~/.repoctx/telemetry/repoctx-events.jsonl` into a per-op digest —
  call counts, success rates, p50/p95 latency, output sizes, daily
  activity histogram, top repos (hashed), surface breakdown, and recent
  errors. Defaults to a 30-day window; pass `--days 0` for all time.
  Output is markdown by default (`--format json` for machine-readable).
  Read-only and privacy-preserving — query and repo-path strings are
  already SHA-256 hashed at write time.

## [0.6.0] — 2026-04-26

Four GitNexus-inspired capabilities, all additive — no schema breaks, no
new dependencies.

### Added
- **`detect_changes` MCP tool + CLI**: maps changed files to direct +
  one-hop transitive callers via the existing import graph. Falls back to
  git's dirty file list when called with no arguments. Surfaces "if I
  touch X, who else needs review?" in one call.
- **Staleness markers**: `bundle()`, `scope()`, and `refresh()` now
  include `staleness` (`head_sha`, `branch`, `dirty_file_count`,
  `dirty_files`, and `dirty_in_scope` for scope-aware ops). Empty `{}` on
  non-git directories.
- **`affected` field on `refresh()`**: same shape as `detect_changes`
  output, so the agent sees blast radius alongside the new scope.
- **`install` CLI + MCP tool**: one-shot setup that runs every harness
  installer (Claude Code, Cursor, Codex) plus the authority scaffold.
  Mirrors GitNexus's `analyze` UX. Idempotent per step; `--no-scaffold`
  skips the contracts/architecture/examples skeleton.
- **`propose_authority` CLI + MCP tool**: scans the repo for subsystems
  and contract surfaces (FastAPI/Flask/Django/Express routes, Pydantic
  models, dataclasses, GraphQL schemas, OpenAPI specs, JSON Schema, plus
  filename hints), then returns a structured `agent_brief` and
  `suggested_files` checklist so an LLM can author real
  `contracts/*.md` and `docs/architecture/*.md` instead of staring at
  empty scaffold templates.
- **AGENTS.md first-time-setup block**: new installs now include
  guidance telling agents to call `propose_authority` when only the
  scaffold is present, closing the bootstrap loop.

## [0.5.1] — 2026-04-26

### Changed
- When the recency log filters down to **exactly one live entry**, the
  resolver now auto-picks it. Single-repo users get zero-friction first
  calls on launchd-spawned hosts (Claude Desktop) without needing to pass
  `repo_root`. Multi-repo users still see the error and pick — the
  multi-repo bug is not reintroduced because >1 live entry continues to
  refuse auto-selection.

## [0.5.0] — 2026-04-25

Robust repo-root resolution for hosts with no workspace context (Claude
Desktop) and safe behavior for users who work across multiple repositories.

### Added
- **Per-call `repo_root` argument** on every MCP tool (`get_task_context`,
  `bundle`, `authority`, `scope`, `validate_plan`, `risk_report`, `refresh`).
  The model can supply the absolute repo path directly — the only signal
  that's reliable when the host hasn't set workspace env vars.
- **Per-process session memoization**: the first successful resolution in
  an MCP server is reused for the lifetime of the process, so the model
  only needs to pass `repo_root` once per Claude Desktop session.
- **Recency log** at `~/.cache/repoctx/recent_repos.json` (multi-entry,
  move-to-front, capped at 10). Used purely to suggest repos in the error
  message when resolution fails — never auto-selected, because in
  multi-repo workflows "the most recent repo" is the wrong default.
- **`$PWD` fallback** for shell-launched hosts that chdir'd to `/` before
  exec while keeping the shell's logical PWD.

### Changed
- Server now boots cleanly even when launched by Claude Desktop with cwd
  `/` and no workspace env vars; resolution is deferred to the first tool
  call. Previously the server crashed at startup with `Server disconnected`.
- Resolution error messages now list recent repos and instruct the model
  to pass `repo_root` rather than only `--repo` / `REPOCTX_REPO_ROOT`.

### Removed
- The single-value `~/.cache/repoctx/last_repo` cache. It silently picked
  the wrong repo when you switched between projects across hosts.

## [0.4.0] — 2026-04-24

Introduces the **v2 ground-truth operating layer** for coding agents. The v1
`get_task_context` tool and its retrieval/embedding core are unchanged; v2 is
an additive layer on top.

### Added
- **Authority discovery** (`repoctx.authority`): classifies files from
  `contracts/`, `docs/architecture/`, `docs/invariants/`, `examples/`,
  `AGENTS.md` / `CLAUDE.md`, and `tests/contracts/`, plus inline
  `INVARIANT:` / `CONTRACT:` / `DO NOT:` / `IMPORTANT:` markers. Parses
  YAML-ish front-matter for `applies_to` / `severity` / `validated_by`.
- **Constraint extraction**: one `Constraint` per bullet under
  `## Invariants` / `## Contracts` / `## Do not` / `## Rules`, with stable
  sha256-based IDs. Soft-wrapped multi-line bullets are joined.
- **Authority graph**: `implemented_by` / `enforced_by` / `points_to` edges
  via fnmatch against `applies_to_paths`.
- **Ground-Truth Bundle** (schema v1, `repoctx-bundle/1`): JSON with
  `authoritative_records`, `constraints`, `relevant_code`, `edit_scope`,
  `validation_plan`, `risk_notes`, and a self-recall contract
  (`when_to_recall_repoctx`, `before_finalize_checklist`, `uncertainty_rule`)
  that is always populated. Markdown renderer included.
- **Six protocol operations**: `bundle`, `authority`, `scope`,
  `validate_plan`, `risk_report` (flags drift when implementing files
  change without the contract), `refresh`. Exposed via CLI and MCP server.
- **Harness installers**: `install-claude-code` (writes `.mcp.json`),
  `install-cursor` (writes `.cursor/mcp.json`), `install-codex` (writes
  `.codex/mcp.json`). Idempotent; preserves existing `AGENTS.md` content
  and other `mcpServers` entries.
- **`init-authority` scaffolder**: seeds `contracts/`,
  `docs/architecture/`, and `examples/` with example front-matter and
  constraint headings.
- **Telemetry**: every protocol op emits a `protocol_op` event with
  hashed `task` / `repo_root` and duration.
- **Own L1 contract**: `contracts/repoctx-protocol.md` — repoctx now
  dogfoods its own authority layer.

### Changed
- `_compute_scope` deprioritizes `__init__.py` / `__main__.py` into
  `related_paths` so the `allowed` set reflects files an agent would
  actually edit.
- `pyproject.toml`: pinned `setuptools.packages.find` to `repoctx*` so
  the new top-level `contracts/` directory doesn't trip flat-layout
  auto-discovery.

### Design
- Full design: `docs/plans/2026-04-23-repoctx-v2-design.md`.

## [0.3.0] — 2026-04

- Resumable `repoctx experiment` wizard with paired control/treatment
  worktrees and MCP stub suppression in the control lane.
- Modular retrieval framework with generic record model.
- Man page + clearer CLI help.
