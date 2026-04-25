# Changelog

All notable changes to `repoctx` are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[SemVer](https://semver.org/).

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
