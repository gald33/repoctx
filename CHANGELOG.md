# Changelog

All notable changes to `repoctx` are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[SemVer](https://semver.org/).

## [Unreleased]

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
