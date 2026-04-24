---
id: repoctx/protocol
severity: hard
applies_to:
  - repoctx/protocol/**
  - repoctx/bundle/**
  - repoctx/authority/**
  - repoctx/harness/**
validated_by:
  - tests/test_protocol_ops.py
  - tests/test_bundle_assembler.py
  - tests/test_bundle_renderer.py
  - tests/test_authority_discovery.py
  - tests/test_authority_extract.py
  - tests/test_authority_scaffold.py
  - tests/test_harness_claude_code.py
  - tests/test_harness_other.py
---

# RepoCtx v2 ground-truth protocol

The six protocol operations and the Ground-Truth Bundle schema are the
contract between RepoCtx and any coding agent that installs it. Downstream
harnesses (Claude Code, Cursor, Codex) and the MCP server rely on this shape
being stable.

## Invariants

- The bundle schema version is `repoctx-bundle/1`; bumping it requires a
  new major-version release and a migration note in the design doc.
- `GroundTruthBundle.when_to_recall_repoctx`, `before_finalize_checklist`,
  and `uncertainty_rule` must always be populated — an empty self-recall
  contract defeats the point of the bundle.
- The six protocol ops are `bundle`, `authority`, `scope`, `validate_plan`,
  `risk_report`, `refresh`. Every op must be exposed via both the CLI and
  the MCP server, and must emit a `protocol_op` telemetry event.
- Authority records are ordered hard → guided → implementation. The bundle
  renderer must emit sections in fixed order: Authority → Constraints →
  Edit scope → Relevant code → Validation plan → Risk notes → When to
  recall → Before you finalize → uncertainty rule.
- Harness installers are idempotent and must never overwrite existing
  `AGENTS.md` content or clobber other `mcpServers` entries.
- Telemetry must hash `task` and `repo_root`; raw task text must never
  leave the local telemetry JSONL.

## Do not

- Do not add a seventh protocol op without updating the MCP registration,
  CLI subcommand list, and the design doc in the same change.
- Do not let `build_bundle` return with an empty self-recall contract.
- Do not introduce a bundle field that is optional at the schema level —
  every field in the JSON output must have a deterministic default.
- Do not write to files outside `contracts/`, `docs/architecture/`,
  `docs/invariants/`, `examples/`, or `AGENTS.md`/`CLAUDE.md` from the
  authority scaffolder.

## Rules

- New authority classifiers (paths, front-matter keys, inline marker
  kinds) must land with tests in `tests/test_authority_*.py`.
- Changes to the bundle schema must update both `bundle/schema.py` and
  `bundle/renderer.py` in the same commit, and carry a renderer test.
