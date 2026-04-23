"""risk_report(task, changed_files) — drift / violation analysis."""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from repoctx.bundle import build_bundle
from repoctx.bundle.schema import RiskNote


def op_risk_report(
    task: str,
    changed_files: list[str],
    repo_root: str | Path = ".",
) -> dict[str, Any]:
    bundle = build_bundle(task, repo_root=repo_root)
    risks: list[RiskNote] = []

    # Protected-path touches.
    for path in changed_files:
        for protected in bundle.edit_scope.protected_paths:
            if _path_matches(path, protected):
                risks.append(
                    RiskNote(
                        risk=f"Change touches protected path: {path}",
                        why=f"Matches edit_scope.protected_paths entry '{protected}'. Confirm with user or authority before proceeding.",
                        severity="hard",
                    )
                )
                break

    # Constraint violations (path-based).
    for c in bundle.constraints:
        if not c.applies_to_paths:
            continue
        hits = [p for p in changed_files if any(fnmatch.fnmatch(p, g) for g in c.applies_to_paths)]
        if not hits:
            continue
        risks.append(
            RiskNote(
                risk=f"Change intersects constraint {c.id}: {c.statement}",
                why=f"Affects {', '.join(hits[:3])}{'…' if len(hits) > 3 else ''}. Severity: {c.severity}.",
                severity=c.severity,
                related_ids=[c.id, c.source_record_id],
            )
        )

    # Missing validation.
    if changed_files and not bundle.validation_plan.tests:
        risks.append(
            RiskNote(
                risk="No validation tests identified for this diff.",
                why="validation_plan.tests is empty; add targeted tests or confirm coverage manually.",
                severity="guided",
            )
        )

    # Possible drift via authority graph: if a contract's implementing file
    # changed but the contract itself did not, flag advisory drift.
    from repoctx.authority.graph import build_authority_graph

    all_repo_paths = set(changed_files)
    all_repo_paths.update(r.path for r in bundle.relevant_code)
    all_repo_paths.update(bundle.edit_scope.allowed_paths)
    all_repo_paths.update(bundle.edit_scope.related_paths)
    graph = build_authority_graph(
        bundle.authoritative_records,
        file_paths=all_repo_paths,
        test_paths=bundle.validation_plan.tests,
    )
    changed_set = set(changed_files)
    contract_paths = {r.path for r in bundle.authoritative_records if r.type == "contract"}
    for record in bundle.authoritative_records:
        if record.type != "contract":
            continue
        if record.path in changed_set:
            continue  # contract itself touched — no drift risk
        implementing = graph.targets("implemented_by", record.id)
        drifted = sorted(implementing & changed_set)
        if drifted:
            risks.append(
                RiskNote(
                    risk=f"Possible drift: implementation changed without updating {record.id}.",
                    why=f"Files {', '.join(drifted[:3])}{'…' if len(drifted) > 3 else ''} are governed by {record.id}; contract file was not touched.",
                    severity="advisory",
                    related_ids=[record.id],
                )
            )

    return {
        "schema_version": "repoctx-bundle/1",
        "task": {"summary": bundle.task_summary, "raw": bundle.task_raw},
        "changed_files": list(changed_files),
        "risk_notes": [r.to_dict() for r in risks],
    }


def _path_matches(path: str, protected: str) -> bool:
    if fnmatch.fnmatch(path, protected):
        return True
    # Treat bare directories as prefixes.
    return path == protected or path.startswith(protected.rstrip("/") + "/")
