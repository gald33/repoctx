# RepoCtx v2 — Ground-Truth Operating Layer for Coding Agents

Status: design
Date: 2026-04-23
Author: design pass grounded in current `repoctx/` tree
Target: evolve repoctx from a repo-context summarizer into a portable ground-truth operating layer.

---

## 0. TL;DR

Today, repoctx answers **"what code is relevant to this task?"** via a scanner + dependency graph + ranked chunks ([repoctx/retriever.py](../../repoctx/retriever.py), [repoctx/context_pack.py](../../repoctx/context_pack.py)) and a generic record store with an embedding provider ([repoctx/core.py](../../repoctx/core.py), [repoctx/record.py](../../repoctx/record.py)).

v2 keeps that core. It adds **authority**, **constraints**, **edit scope**, **validation plans**, **risk reports**, and a **self-recall contract** on top, so an agent that can only be influenced through its repo (AGENTS.md) and the bundle output can still stay aligned with ground truth.

v2 is **not** a rewrite. It is three additions:

1. **Authority layer** — new record types (`contract`, `invariant`, `example`, `agent_instruction`, `architecture_note`, `validation_rule`), discovered by a new `AuthorityProducer`, indexed through the existing `RecordStore`.
2. **Bundle layer** — `GroundTruthBundle` assembler that consumes authority + existing retrieval + dep graph, returns a structured, authority-first, token-aware bundle with scope, validation plan, risk notes, and **self-recall rules**.
3. **Protocol layer** — six MCP/CLI operations: `bundle`, `authority`, `scope`, `validate_plan`, `risk_report`, `refresh`. One new adapter directory `repoctx/harness/` for harness-specific glue (Claude Code first).

The existing `get_task_context` tool remains; `bundle` supersedes it for coding-agent tasks and delegates to it for the "relevant code" section.

---

## 1. v2 Checklist (what must ship)

- [ ] Typed authority records (`contract`, `invariant`, `example`, `agent_instruction`, `architecture_note`, `validation_rule`) with `authority_level` ∈ {1,2,3}.
- [ ] `AuthorityProducer` that discovers authority sources from conventional paths + inline markers.
- [ ] `Constraint` first-class object, extracted from authority records via marker parsing.
- [ ] `AuthorityGraph` relations: `implemented_by`, `enforced_by`, `validates`, `points_to`, `constrained_by`.
- [ ] `GroundTruthBundle` dataclass + JSON schema + markdown renderer.
- [ ] Bundle assembler: authority-first, token-budgeted, with `edit_scope`, `validation_plan`, `risk_notes`, `when_to_recall_repoctx`, `before_finalize_checklist`, `uncertainty_rule`.
- [ ] Six protocol operations: `bundle`, `authority`, `scope`, `validate_plan`, `risk_report`, `refresh`.
- [ ] MCP tools for each, plus CLI subcommands.
- [ ] `repoctx/harness/claude_code.py` adapter: AGENTS.md template generator + recall-rule formatter.
- [ ] AGENTS.md convention doc (this repo eats its own dog food).
- [ ] Tests for each layer in `tests/`.

Non-goals (explicitly deferred): autonomous editing, embeddings-only retrieval, required harness hooks, large ontologies, cross-environment perfection in v1.

---

## 2. Architectural Specification

### 2.1 Layer diagram

```
┌──────────────────────────────────────────────────────────────┐
│ Harness adapters (repoctx/harness/*)                          │
│   claude_code.py · cursor.py · codex.py                       │
└──────────────────────────────────────────────────────────────┘
                ▲                          ▲
                │ calls                    │ renders
                │                          │
┌──────────────────────────────────────────────────────────────┐
│ Protocol layer (repoctx/protocol/*)                           │
│   bundle · authority · scope · validate_plan · risk · refresh │
└──────────────────────────────────────────────────────────────┘
                ▲
                │ composes
┌──────────────────────────────────────────────────────────────┐
│ Ground-Truth Bundle assembler (repoctx/bundle/*)              │
│   assembler.py · renderer.py · schema.py                      │
└──────────────────────────────────────────────────────────────┘
                ▲               ▲               ▲
                │               │               │
┌──────────────┴─────────────┐ ┌┴──────────────┐ ┌─────────────┐
│ Authority layer            │ │ Retrieval     │ │ Dep graph   │
│ repoctx/authority/*        │ │ (existing     │ │ (existing   │
│  · discovery.py            │ │  core.py      │ │  graph.py)  │
│  · records.py              │ │  retriever.py)│ │             │
│  · constraints.py          │ │               │ │             │
│  · graph.py                │ │               │ │             │
└────────────────────────────┘ └───────────────┘ └─────────────┘
                ▲
                │ produces RetrievableRecord
┌──────────────────────────────────────────────────────────────┐
│ Generic core (unchanged): record.py · core.py · vector_index  │
└──────────────────────────────────────────────────────────────┘
```

### 2.2 Authority discovery

Inputs:
- Conventional paths: `AGENTS.md`, `AGENT.md`, `CLAUDE.md`, `/contracts/**`, `/schemas/**`, `/examples/**`, `/docs/architecture/**`, `/docs/invariants/**`, `/docs/adr/**`.
- Inline markers in any file: `INVARIANT:`, `CONTRACT:`, `IMPORTANT:`, `DO NOT:`, `See contract: <path>`.
- Test discovery: tests whose names match `test_contract_*`, `test_invariant_*`, or which import a module from `/contracts/` — tagged as `validation_rule`.

Output: `list[AuthorityRecord]` that are also valid `RetrievableRecord`s (so they go through the same embedding + filter pipeline).

### 2.3 Authority levels

| Level | Meaning | Example paths |
|-------|---------|---------------|
| 1 — hard | Breaking it is a bug, even if tests pass | `/contracts/**`, `/schemas/**`, files matching `*.invariant.md`, inline `INVARIANT:` blocks, golden-behavior tests |
| 2 — guided | Agent should follow unless overridden with reason | `AGENTS.md`, `/docs/architecture/**`, `/docs/adr/**`, inline `IMPORTANT:` blocks |
| 3 — implementation | Normal code, neighbors, tests | everything else |

Level is computed by the discoverer, stored in metadata, and used by the bundle assembler to sort + truncate.

### 2.4 Constraints

Constraints are extracted, not just displayed. A `Constraint` is:

```python
@dataclass
class Constraint:
    id: str                       # stable hash of source + statement
    statement: str                # normalized one-line rule
    source_record_id: str
    scope: Literal["global", "path", "module", "subsystem"]
    applies_to_paths: list[str]   # glob patterns
    severity: Literal["hard", "guided", "advisory"]
    validation_refs: list[str]    # record_ids of tests/examples that enforce it
```

Extraction rules (initial, pragmatic):
- Markdown bullet under an `## Invariants` / `## Contracts` / `## Do not` heading → one constraint per bullet.
- Inline marker lines (`# INVARIANT: ...`, `// INVARIANT: ...`) → one constraint per line, scoped to the containing file.
- Contract files under `/contracts/foo.md`: the whole file is one constraint with scope from a front-matter `applies_to:` list, or inferred from matching filename (`/contracts/auth.md` → `**/auth/**`).

### 2.5 Authority graph

Edges:
- `agent_instruction --points_to--> contract|invariant`
- `contract --implemented_by--> code_chunk`
- `invariant --enforced_by--> test_chunk`
- `example --validates--> contract`
- `code_chunk --constrained_by--> constraint`

Edges are cheap to compute:
- `points_to`: "See contract: X" regex + link resolution.
- `implemented_by`: filename/identifier overlap + dep-graph import from contract's `applies_to_paths`.
- `enforced_by`: test file imports a path under `applies_to_paths` or filename matches `test_<constraint_id>`.
- `validates`: example files that reference a contract by path or id.
- `constrained_by`: for each code file, all constraints whose `applies_to_paths` match.

The graph is stored as two `dict[str, set[str]]` (forward/reverse), same shape as the existing `DependencyGraph` in [repoctx/models.py](../../repoctx/models.py).

### 2.6 Retrieval modes

Added as flags on `RetrievalQuery`-adjacent helpers, not by forking retrieval:

- `task_context` — current default, relevance only.
- `implementation_context` — code + tests only.
- `ground_truth_context` — authority records only, level 1 then 2.
- `change_safety_context` — constraints + protected paths intersecting a changed-file list.
- `validation_context` — tests + examples linked to authority.

The bundle assembler runs `ground_truth_context` first, `task_context` second, then prunes `task_context` entries already covered by authority neighbors.

---

## 3. Ground Truth Bundle Schema

JSON is primary. Markdown renderer is a derived view for UIs.

```jsonc
{
  "schema_version": "repoctx-bundle/1",
  "task": {
    "summary": "string, <= 240 chars, reformulated from input",
    "raw": "string, original task text"
  },
  "authority": {
    "records": [
      {
        "id": "contract:auth/session-tokens",
        "type": "contract",
        "authority_level": 1,
        "path": "contracts/auth/session-tokens.md",
        "title": "Session-token storage contract",
        "summary": "Session tokens must never be persisted to disk outside the encrypted vault.",
        "excerpt": "string, <= 800 chars, authority-first",
        "tags": ["auth", "security"],
        "related_ids": ["test:tests/contracts/test_session_tokens.py"]
      }
    ],
    "constraints": [
      {
        "id": "const:9a1f...",
        "statement": "Session tokens must not be written to disk unencrypted.",
        "source_record_id": "contract:auth/session-tokens",
        "scope": "subsystem",
        "applies_to_paths": ["app/auth/**"],
        "severity": "hard",
        "validation_refs": ["test:tests/contracts/test_session_tokens.py"]
      }
    ]
  },
  "relevant_code": [
    { "path": "app/auth/session.py", "reason": "...", "score": 12.1, "snippet": "..." }
  ],
  "examples": [
    { "path": "examples/auth/session_refresh.py", "reason": "validates contract:auth/session-tokens" }
  ],
  "edit_scope": {
    "allowed_paths":   ["app/auth/**", "tests/auth/**"],
    "related_paths":   ["app/middleware/auth_mw.py"],
    "protected_paths": ["contracts/**", "schemas/**", "app/auth/crypto.py"],
    "rationale": "Task touches session handling; crypto primitives are frozen (see const:...)."
  },
  "validation_plan": {
    "commands":         ["pytest tests/auth -q", "ruff check app/auth"],
    "tests":            ["tests/contracts/test_session_tokens.py"],
    "contract_checks":  ["contract:auth/session-tokens"],
    "invariants_to_verify": ["const:9a1f..."]
  },
  "risk_notes": [
    { "risk": "Touching app/auth/crypto.py", "why": "Listed as protected_path; covered by const:...", "severity": "hard" }
  ],
  "when_to_recall_repoctx": [
    "If you need to edit any path not in edit_scope.allowed_paths, call scope(task) first.",
    "If you discover a new subsystem dependency (e.g. new import from app/billing), call refresh(task, changed_files).",
    "If implementation appears to conflict with an example in authority.related_ids, call authority(task) and re-read."
  ],
  "before_finalize_checklist": [
    "Call validate_plan(task, changed_files) and run every command it returns.",
    "Call risk_report(task, changed_files); resolve every 'hard' severity item.",
    "Ensure no path in edit_scope.protected_paths changed unintentionally."
  ],
  "uncertainty_rule": "If unsure whether a change violates a constraint, call repoctx.authority(task) instead of guessing.",
  "metrics": {
    "authority_records": 3,
    "constraints": 4,
    "relevant_code": 8,
    "output_bytes": 6421,
    "scan_duration_ms": 410
  }
}
```

Notes:
- `excerpt` is bounded; full text is fetched on demand through `authority(task, include="full")`.
- `when_to_recall_repoctx`, `before_finalize_checklist`, `uncertainty_rule` are **always present**. They are the self-recall contract (§ 5).
- `schema_version` is mandatory so harness adapters can evolve independently.

---

## 4. Protocol — six operations

All six take a `task: str` at minimum. All return JSON. Each has a matching MCP tool and a CLI subcommand.

| Op | Purpose | Typical cost | Key fields returned |
|----|---------|-------------|---------------------|
| `bundle(task)` | Primary call — complete ground-truth bundle | ~3–8 KB | full schema above |
| `authority(task, include?)` | Authority records + constraints only | ~1–2 KB | `authority.*` only |
| `scope(task)` | Edit-scope decision support | ~0.5–1 KB | `edit_scope`, `rationale` |
| `validate_plan(task, changed_files)` | Tests/commands to run given actual diff | ~0.5 KB | `validation_plan` |
| `risk_report(task, changed_files)` | Drift/violation analysis of a diff | ~0.5–1 KB | `risk_notes`, protected-path touches |
| `refresh(task, changed_files, current_scope)` | Incremental bundle update when scope grows | ~1–2 KB | delta: added authority/constraints/scope |

Arguments:
- `changed_files: list[str]` — paths relative to repo root; the caller is responsible for passing the actual diff.
- `current_scope: EditScope | None` — what the caller believed scope was; `refresh` returns a diff against it.
- `include`: `"summary"` (default, excerpt only) | `"full"` (full text, more tokens).

### 4.1 Token-aware call policy (what the agent should actually do)

This is the default policy encoded in every bundle's `when_to_recall_repoctx` and in the AGENTS.md template:

1. **Task start:** `bundle(task)`. One call.
2. **If scope expands** (agent wants to edit a path outside `edit_scope.allowed_paths`): `refresh(task, changed_files, current_scope)`. At most 1–2 calls per task.
3. **Before finalizing:** `validate_plan(task, changed_files)` → run the commands → `risk_report(task, changed_files)`. Two calls, together.
4. **On uncertainty** about a specific constraint: `authority(task)`. Rare.

Ceiling: **≤ 5 repoctx calls per task** in typical flows. The bundle is designed to make that enough.

---

## 5. Self-Recall Contract

The three fields `when_to_recall_repoctx`, `before_finalize_checklist`, `uncertainty_rule` are non-optional. They exist because in practice we cannot guarantee the harness calls repoctx at the right moments — the agent itself must.

Generation rules for `when_to_recall_repoctx`:
- Always include: scope-expansion, finalize, uncertainty-on-constraint.
- Conditionally include: if `protected_paths` is non-empty → add explicit line; if any constraint's `severity == "hard"` → add line about that constraint by id.
- Generated bullets are concrete (reference a named op, a named scope, a named constraint id), not generic ("think carefully").

Generation rules for `before_finalize_checklist`:
- Always: `validate_plan`, `risk_report`, protected-path check.
- Conditionally: if validation_plan commands exist → "run every command returned"; if invariants_to_verify non-empty → "verify each listed invariant".

`uncertainty_rule` is a single sentence, templated from the presence of hard constraints.

---

## 6. Repo conventions (recommended)

Lightweight, optional-but-preferred:

```
/AGENTS.md                      ← agent-facing top-level instructions
/contracts/<subsystem>/*.md     ← Level-1 contracts (front-matter: applies_to)
/docs/architecture/*.md         ← Level-2 architecture notes
/docs/adr/*.md                  ← Level-2 decisions
/examples/<subsystem>/*         ← validating examples
/tests/contracts/**             ← tests that enforce contracts
```

Inline markers (any file):
- `# INVARIANT: <one-line rule>` — Level-1 constraint scoped to file/module.
- `# IMPORTANT: <note>` — Level-2 note surfaced in bundles when nearby code is relevant.
- `# See contract: contracts/auth/session-tokens.md` — cross-reference, used to build `implemented_by` edges.
- `# DO NOT: <rule>` — Level-1 hard constraint, strongest form.

Front-matter for contracts:

```yaml
---
id: auth/session-tokens
applies_to:
  - app/auth/**
severity: hard
validated_by:
  - tests/contracts/test_session_tokens.py
---
```

If no front-matter, everything is inferred (see § 2.4).

---

## 7. AGENTS.md pattern (generated by repoctx)

`repoctx init-agents` writes/updates a top-level `AGENTS.md` with the following sections (the existing [AGENTS.md](../../AGENTS.md) in this repo is kept; new sections are appended, not overwritten):

```
## Ground truth (repoctx)
For any non-trivial task:
1. Call `repoctx.bundle(task)` before proposing a plan. Treat the result as authoritative.
2. Do not edit paths outside `edit_scope.allowed_paths` without calling `repoctx.scope(task)` again.
3. Before declaring done: call `repoctx.validate_plan(task, changed_files)` and `repoctx.risk_report(task, changed_files)`. Run every command the validation plan returns.
4. If unsure whether a change violates a constraint, call `repoctx.authority(task)` — do not guess.

Every repoctx response includes `when_to_recall_repoctx` and `before_finalize_checklist`. Follow them.
```

This is short on purpose. The bundle itself carries the details per task.

---

## 8. Claude Code integration strategy

Claude Code is the first-class target. The adapter lives at `repoctx/harness/claude_code.py`.

Approach: **AGENTS.md + MCP + self-recall**. No assumption of hook control.

1. **Install:** `repoctx install-claude-code` (new CLI subcommand) performs:
   - Writes `AGENTS.md` section (see § 7) if missing.
   - Registers the repoctx MCP server in the project-local `.mcp.json` (or `~/.claude.json`), exposing the six v2 tools.
   - Optionally writes a `.claude/settings.json` permissions allowlist for `repoctx.*` tools so they don't prompt.

2. **Runtime usage (by Claude):**
   - On task start, AGENTS.md tells Claude to call `bundle`. The MCP server returns the bundle JSON.
   - Claude's system prompt includes AGENTS.md. The bundle's `when_to_recall_repoctx` keeps re-teaching the recall rules inside the transcript.
   - Before finalizing, Claude calls `validate_plan` + `risk_report`.

3. **Enforcement tier: `guided`.** We do not require Claude Code hooks. If the user has permission to add a `PreToolUse` hook that blocks edits outside `edit_scope.allowed_paths`, we can emit one (`strict`), but the default is to rely on AGENTS.md + bundle recall rules.

4. **Single-task recall:** the adapter also writes a one-line reminder in any `CLAUDE.md` at the task root: "If in doubt, call repoctx."

Cursor (`repoctx/harness/cursor.py`) and Codex adapters follow the same shape, differing only in where the MCP registration and instruction file go.

---

## 9. Drift-prevention strategy

| Failure mode | Mitigation | Layer |
|--------------|-----------|-------|
| Agent re-derives truth instead of following it | Bundle surfaces authority first; AGENTS.md tells agent to call `bundle` first | Bundle + AGENTS |
| Already-verified behavior silently changes | `protected_paths` + `risk_report` flags touches | Scope + Risk |
| Local edits break cross-file invariants | Constraints with `applies_to_paths` + `constrained_by` edges → risk_report flags violations given diff | Constraints + Risk |
| Contracts drift from implementations | `implemented_by` edges; drift warning when a contract's linked files change without the contract changing, and vice versa | Authority graph + Risk |
| Protected flows keep breaking | Hard constraints always surfaced in bundle; `before_finalize_checklist` mandates validation | Self-recall contract |

Mechanically, drift detection is implemented in `risk_report`:
- For each file in `changed_files`, look up all constraints whose `applies_to_paths` match.
- For each such constraint, return `constraint_risk` with the constraint id, statement, and (if present) the matching `validation_refs`.
- Separately: if any `changed_files` overlaps `protected_paths` → `protected_path_touch` risk.
- Separately: if any contract's `implemented_by` set intersects `changed_files` but the contract file itself did not change → `possible_drift` risk (advisory).

---

## 10. Module & file layout

New files under `repoctx/`:

```
repoctx/
  authority/
    __init__.py
    records.py          # AuthorityRecord, types, level enum
    discovery.py        # AuthorityProducer(RecordProducer)
    constraints.py      # Constraint extraction from records
    graph.py            # AuthorityGraph (forward/reverse)
    markers.py          # INVARIANT:/IMPORTANT:/See contract: parsing
  bundle/
    __init__.py
    schema.py           # GroundTruthBundle dataclass + JSON schema
    assembler.py        # build_bundle(task, ...)
    recall.py           # when_to_recall / checklist / uncertainty_rule
    renderer.py         # markdown view
  protocol/
    __init__.py
    bundle_op.py
    authority_op.py
    scope_op.py
    validate_op.py
    risk_op.py
    refresh_op.py
  harness/
    __init__.py
    claude_code.py      # install + AGENTS section template
    cursor.py
    codex.py
```

Files touched (not rewritten):
- [repoctx/mcp_server.py](../../repoctx/mcp_server.py) — add five new `@server.tool()` registrations calling into `protocol/*`.
- [repoctx/main.py](../../repoctx/main.py) — add six CLI subcommands + `init-agents`, `install-claude-code`.
- [repoctx/adapters/repo.py](../../repoctx/adapters/repo.py) — no change; `AuthorityProducer` is a sibling producer, not a subclass.
- [repoctx/record.py](../../repoctx/record.py) — no change; authority records are `RetrievableRecord` instances with specific `record_type` values.
- [repoctx/core.py](../../repoctx/core.py) — no change.

Tests: one file per new module, mirroring existing conventions in [tests/](../../tests/).

---

## 11. Phased implementation plan

**Phase 1 — authority + typed records (1–2 days of work)**
- `repoctx/authority/records.py`, `discovery.py`, `markers.py`.
- Convention-based path discovery + inline marker parsing.
- `AuthorityProducer.build_records()` returning `RetrievableRecord`s with `record_type` ∈ {contract, invariant, example, agent_instruction, architecture_note, validation_rule} and `metadata.authority_level`.
- `tests/test_authority_discovery.py`, `tests/test_authority_markers.py`.

**Phase 2 — bundle assembler (1–2 days)**
- `repoctx/bundle/schema.py`, `assembler.py`, `recall.py`, `renderer.py`.
- Assembler reuses `get_task_context_data` for `relevant_code` section.
- Token budget: soft cap 8 KB, truncate `relevant_code` first, never `authority`.
- `tests/test_bundle_assembler.py` (authority always ranks first; recall fields always present).

**Phase 3 — scope + validate_plan (1 day)**
- `repoctx/authority/constraints.py` + `protocol/scope_op.py`, `validate_op.py`.
- Scope computed from: seed files' parents + constraints' `applies_to_paths` (protected) + retrieval top-k (allowed).
- Validate plan computed from: `validation_refs` on matched constraints + tests in `related_tests`.
- Tests.

**Phase 4 — risk_report + refresh (1 day)**
- `protocol/risk_op.py`, `refresh_op.py`.
- Risk rules from § 9.
- Refresh returns delta vs. `current_scope`.
- Tests including drift detection case.

**Phase 5 — Claude Code adapter (0.5 day)**
- `repoctx/harness/claude_code.py` + `install-claude-code` CLI.
- Idempotent AGENTS.md append, `.mcp.json` registration.
- Tests that exercise the installer against a temp repo.

**Phase 6 — authority graph + drift warnings (1 day, optional for v2.0)**
- `repoctx/authority/graph.py` + integration into `risk_op`.
- Adds `possible_drift` risks and `implemented_by`/`enforced_by`/`validates` edges to the bundle.
- Tests.

Total: ~1 week of focused work, shippable incrementally. Phases 1–4 are the minimum viable v2. Phase 5 makes it usable in Claude Code without the user configuring anything. Phase 6 is the drift story.

---

## 12. What we reuse from v1

- [repoctx/record.py](../../repoctx/record.py) — `RetrievableRecord`, `RetrievalQuery`, `MetadataFilter`. Authority records are just records with specific `record_type` + metadata.
- [repoctx/core.py](../../repoctx/core.py) — `RecordStore`, `EmbeddingProvider`, `RecordProducer` protocol. `AuthorityProducer` implements the same protocol.
- [repoctx/scanner.py](../../repoctx/scanner.py) — single-pass repo walk; authority discovery piggybacks on it instead of walking again.
- [repoctx/graph.py](../../repoctx/graph.py) — import dependency graph, used by scope + validate_plan.
- [repoctx/retriever.py](../../repoctx/retriever.py) — heuristic + embedding ranking; bundle assembler calls `get_task_context_data` for the `relevant_code` section.
- [repoctx/telemetry.py](../../repoctx/telemetry.py) — extended to emit one event per protocol op so we can measure "calls per task" and tune the default policy.
- [repoctx/experiment.py](../../repoctx/experiment.py) — v2 bundle becomes a new experiment variant (treatment), comparable against current `get_task_context` (baseline) and no-repoctx (control).

---

## 13. Open questions

1. **Where does embedding add value for authority records?** Constraint matching against a diff is mostly path-glob based. Embedding might help for "which constraint does this task free-text imply?" — worth A/B-ing in an experiment lane.
2. **How strict should `protected_paths` be?** Advisory for v2.0; strict enforcement (via hook) is a harness concern.
3. **How do we handle monorepos with per-subsystem AGENTS.md?** v2 discovers the nearest AGENTS.md upward from the task's seed files and merges with root. Defer the merge semantics to v2.1 if the initial cases are rare.
4. **Bundle caching.** Per-repo-commit cache keyed by `(task_hash, commit_sha)`. Defer to v2.1 unless token cost bites during dogfooding.

---

## 14. Success criteria

- A Claude Code session in this repo, with only AGENTS.md + the MCP server installed, produces edits that respect all repo-level constraints on ≥ 90% of a held-out task set **without** the human reminding it of any constraint.
- Median repoctx calls per task: ≤ 3. P95: ≤ 5.
- Median bundle size: ≤ 6 KB.
- Risk-report false positive rate (human-judged): ≤ 15%.
