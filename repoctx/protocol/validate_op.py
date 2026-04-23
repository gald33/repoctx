"""validate_plan(task, changed_files) — tests & commands given a diff."""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from repoctx.bundle import build_bundle
from repoctx.bundle.schema import ValidationPlan


def op_validate_plan(
    task: str,
    changed_files: list[str],
    repo_root: str | Path = ".",
) -> dict[str, Any]:
    bundle = build_bundle(task, repo_root=repo_root)
    base = bundle.validation_plan

    matched_constraints = _constraints_matching_paths(bundle.constraints, changed_files)
    extra_tests = sorted({ref.split(":", 1)[1] for c in matched_constraints for ref in c.validation_refs if ref.startswith("test:")})
    tests = sorted(set(base.tests) | set(extra_tests))

    commands = list(base.commands)
    if tests and not any(cmd.startswith("pytest") for cmd in commands):
        commands.append("pytest -q " + " ".join(tests))

    plan = ValidationPlan(
        commands=commands,
        tests=tests,
        contract_checks=sorted(set(base.contract_checks) | {c.source_record_id for c in matched_constraints if c.source_record_id.startswith("contract:")}),
        invariants_to_verify=sorted(set(base.invariants_to_verify) | {c.id for c in matched_constraints if c.severity == "hard"}),
    )
    return {
        "schema_version": "repoctx-bundle/1",
        "task": {"summary": bundle.task_summary, "raw": bundle.task_raw},
        "validation_plan": plan.to_dict(),
        "changed_files": list(changed_files),
    }


def _constraints_matching_paths(constraints, changed_files: list[str]):
    matched = []
    for c in constraints:
        if not c.applies_to_paths:
            continue
        for path in changed_files:
            if any(fnmatch.fnmatch(path, glob) for glob in c.applies_to_paths):
                matched.append(c)
                break
    return matched
