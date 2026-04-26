"""Generate a brief that lets an LLM author the authority files itself.

`init_authority` only writes empty templates. The actual value — turning a
repo into well-described contracts + architecture notes — is exactly the
kind of work an agent is good at. ``propose_authority`` scans the repo,
detects likely contract surfaces and subsystem boundaries, and returns:

- a concrete checklist of files to write (path + topic + 1-line rationale)
- a list of detected subsystems
- a list of detected contract candidates (route handlers, schemas, models)
- a markdown brief telling the agent the conventions and how to fill in
  each file

The agent then uses its normal Write/Edit tools to author the files. The
brief is structured so the agent can act on it without further prompting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from repoctx.config import DEFAULT_CONFIG, RepoCtxConfig
from repoctx.scanner import scan_repository

# Patterns that suggest a file defines an external/internal contract.
_CONTRACT_SIGNALS: tuple[tuple[str, str], ...] = (
    ("fastapi_route", r"@\w+\.(get|post|put|delete|patch)\("),
    ("flask_route", r"@\w+\.route\("),
    ("django_url", r"path\(\s*['\"]"),
    ("express_route", r"\b(?:app|router)\.(get|post|put|delete|patch)\("),
    ("pydantic_model", r"class\s+\w+\(\s*BaseModel\s*\)"),
    ("dataclass_schema", r"@dataclass[\s\S]{0,80}class\s+\w+"),
    ("graphql_schema", r"\b(type|input|interface)\s+\w+\s*\{"),
    ("openapi_spec", r"openapi:\s*['\"]?3\."),
    ("json_schema", r'"\$schema"\s*:\s*"http'),
)

_SCHEMA_FILENAME_HINTS = ("schema", "models", "types", "openapi", "swagger", "contract")


@dataclass(slots=True)
class ContractCandidate:
    path: str
    signals: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Subsystem:
    name: str
    path: str
    file_count: int
    sample_files: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SuggestedFile:
    path: str
    topic: str
    rationale: str
    template_hint: str  # "contract" | "architecture" | "example"


def propose_authority(
    repo_root: str | Path = ".",
    config: RepoCtxConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    repo_path = Path(repo_root).resolve()
    index = scan_repository(repo_path, config=config)

    subsystems = _detect_subsystems(index)
    candidates = _detect_contract_candidates(index)
    existing = _detect_existing_authority(repo_path)
    suggested = _build_suggestions(subsystems, candidates, existing)

    return {
        "schema_version": "repoctx-bundle/1",
        "repo_root": str(repo_path),
        "existing_authority": existing,
        "subsystems": [
            {
                "name": s.name,
                "path": s.path,
                "file_count": s.file_count,
                "sample_files": s.sample_files,
            }
            for s in subsystems
        ],
        "contract_candidates": [
            {"path": c.path, "signals": c.signals} for c in candidates
        ],
        "suggested_files": [
            {
                "path": f.path,
                "topic": f.topic,
                "rationale": f.rationale,
                "template_hint": f.template_hint,
            }
            for f in suggested
        ],
        "agent_brief": _render_brief(subsystems, candidates, suggested, existing),
    }


# ---- detection ----------------------------------------------------------


def _detect_subsystems(index) -> list[Subsystem]:
    """Top-level code directories with >= 3 files. These become arch notes."""
    by_top: dict[str, list[str]] = {}
    for record in index.code_files:
        parts = record.path.split("/", 1)
        if len(parts) < 2:
            continue
        top = parts[0]
        if top in {"tests", "test", "examples", "docs", "contracts"}:
            continue
        by_top.setdefault(top, []).append(record.path)

    out: list[Subsystem] = []
    for name, files in sorted(by_top.items()):
        if len(files) < 3:
            continue
        out.append(
            Subsystem(
                name=name,
                path=name,
                file_count=len(files),
                sample_files=sorted(files)[:5],
            )
        )
    return out


def _detect_contract_candidates(index) -> list[ContractCandidate]:
    candidates: list[ContractCandidate] = []
    seen: set[str] = set()
    for record in index.code_files + index.config_files:
        signals: list[str] = []
        name_lower = Path(record.path).name.lower()
        if any(hint in name_lower for hint in _SCHEMA_FILENAME_HINTS):
            signals.append("filename_hint")
        for label, pattern in _CONTRACT_SIGNALS:
            if re.search(pattern, record.content):
                signals.append(label)
        if signals and record.path not in seen:
            seen.add(record.path)
            candidates.append(ContractCandidate(path=record.path, signals=signals))
    # Cap to keep the brief actionable.
    return candidates[:25]


def _detect_existing_authority(repo_path: Path) -> dict[str, Any]:
    """Distinguish 'scaffold-only' from 'real content' so we don't double-suggest."""
    info: dict[str, Any] = {
        "contracts": [],
        "architecture": [],
        "agents_md": False,
    }
    contracts_dir = repo_path / "contracts"
    if contracts_dir.is_dir():
        for p in sorted(contracts_dir.glob("*.md")):
            if p.name in {"README.md", "example.md"}:
                continue
            info["contracts"].append(p.relative_to(repo_path).as_posix())
    arch_dir = repo_path / "docs" / "architecture"
    if arch_dir.is_dir():
        for p in sorted(arch_dir.glob("*.md")):
            if p.name in {"README.md", "example.md"}:
                continue
            info["architecture"].append(p.relative_to(repo_path).as_posix())
    info["agents_md"] = (repo_path / "AGENTS.md").exists()
    return info


def _build_suggestions(
    subsystems: list[Subsystem],
    candidates: list[ContractCandidate],
    existing: dict[str, Any],
) -> list[SuggestedFile]:
    out: list[SuggestedFile] = []
    existing_contracts = {Path(p).stem for p in existing.get("contracts", [])}
    existing_arch = {Path(p).stem for p in existing.get("architecture", [])}

    # Architecture note per detected subsystem.
    for s in subsystems:
        if s.name in existing_arch:
            continue
        out.append(
            SuggestedFile(
                path=f"docs/architecture/{s.name}.md",
                topic=f"How the `{s.name}/` subsystem is organized and what it owns",
                rationale=(
                    f"Detected {s.file_count} code files under `{s.name}/`. "
                    "Capture the boundary, key entry points, and intended "
                    "consumers so agents working here have ground truth."
                ),
                template_hint="architecture",
            )
        )

    # Contract per candidate cluster (group by top-level dir).
    by_top: dict[str, list[ContractCandidate]] = {}
    for c in candidates:
        top = c.path.split("/", 1)[0]
        by_top.setdefault(top, []).append(c)
    for top, group in sorted(by_top.items()):
        stem = f"{top}-contract"
        if stem in existing_contracts or top in existing_contracts:
            continue
        out.append(
            SuggestedFile(
                path=f"contracts/{top}.md",
                topic=f"Hard invariants for `{top}/` (routes, schemas, models)",
                rationale=(
                    f"Detected {len(group)} contract-bearing file(s): "
                    f"{', '.join(c.path for c in group[:3])}"
                    f"{'…' if len(group) > 3 else ''}. "
                    "Spell out the invariants these encode so agents cannot "
                    "silently break them."
                ),
                template_hint="contract",
            )
        )

    return out


def _render_brief(
    subsystems: list[Subsystem],
    candidates: list[ContractCandidate],
    suggested: list[SuggestedFile],
    existing: dict[str, Any],
) -> str:
    lines: list[str] = []
    lines.append("# Authority bootstrap brief\n")
    lines.append(
        "You are filling in this repo's authority layout so `repoctx` can "
        "give downstream agents accurate ground truth. Work through the "
        "checklist below. Use your `Read`/`Write` tools.\n"
    )

    lines.append("## Conventions\n")
    lines.append(
        "- **Contracts** (`contracts/*.md`) are Level-1 hard authority. "
        "Each file has YAML front-matter (`id`, `severity: hard`, `applies_to: [glob, ...]`) "
        "followed by `## Invariants` and `## Do not` bullet lists. "
        "Each bullet becomes a `Constraint` surfaced in `repoctx.bundle(task)`.\n"
        "- **Architecture notes** (`docs/architecture/*.md`) are Level-2 guided authority. "
        "Narrative; describe boundaries, entry points, intended consumers.\n"
        "- **Examples** (`examples/*`) are Level-2 demonstrations of contracts in action.\n"
    )

    if existing["contracts"] or existing["architecture"]:
        lines.append("## Already in the repo (do not overwrite)\n")
        for p in existing["contracts"]:
            lines.append(f"- `{p}`")
        for p in existing["architecture"]:
            lines.append(f"- `{p}`")
        lines.append("")

    if subsystems:
        lines.append("## Detected subsystems\n")
        for s in subsystems:
            lines.append(
                f"- **`{s.name}/`** — {s.file_count} files. Sample: "
                f"{', '.join(f'`{p}`' for p in s.sample_files[:3])}"
            )
        lines.append("")

    if candidates:
        lines.append("## Detected contract surfaces\n")
        for c in candidates[:15]:
            lines.append(f"- `{c.path}` — signals: {', '.join(c.signals)}")
        if len(candidates) > 15:
            lines.append(f"- …and {len(candidates) - 15} more")
        lines.append("")

    if suggested:
        lines.append("## Files to write\n")
        for f in suggested:
            lines.append(f"### `{f.path}`")
            lines.append(f"- **Topic:** {f.topic}")
            lines.append(f"- **Why:** {f.rationale}")
            lines.append(f"- **Template:** see the `{f.template_hint}` shape in `contracts/README.md` or `docs/architecture/README.md`.")
            lines.append("")

    lines.append("## How to do it well\n")
    lines.append(
        "1. Read 2–3 of the sample files for each subsystem before writing — "
        "describe what is actually true, not what should be true.\n"
        "2. Invariants must be testable. Prefer `must`/`must not` phrasing.\n"
        "3. Keep each contract under 30 lines. If it grows past that, split it.\n"
        "4. After writing, run `repoctx authority \"sanity check\"` to confirm the "
        "records load and constraints are extracted as expected.\n"
        "5. Commit the new files separately from any code changes so the "
        "authority history is auditable.\n"
    )
    return "\n".join(lines)


__all__ = ["propose_authority"]
