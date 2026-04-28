# Changelog

All notable changes to `repoctx` are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[SemVer](https://semver.org/).

## [Unreleased]

### Added
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
