"""Self-recall contract generation.

See ``docs/plans/2026-04-23-repoctx-v2-design.md`` § 5.
"""

from __future__ import annotations

from repoctx.authority.constraints import Constraint
from repoctx.bundle.schema import EditScope, ValidationPlan


def when_to_recall_repoctx(
    *,
    edit_scope: EditScope,
    constraints: list[Constraint],
) -> list[str]:
    rules = [
        "If you need to edit a path not in edit_scope.allowed_paths, call repoctx.scope(task) and then repoctx.refresh(task, changed_files, current_scope).",
        "If you discover a new subsystem dependency (new import, new module), call repoctx.refresh(task, changed_files, current_scope).",
        "Before finalizing, call repoctx.validate_plan(task, changed_files) and repoctx.risk_report(task, changed_files).",
    ]
    if edit_scope.protected_paths:
        rules.append(
            "If you think you must modify any path in edit_scope.protected_paths, call repoctx.authority(task) first and confirm with the user."
        )
    hard_ids = [c.id for c in constraints if c.severity == "hard"]
    if hard_ids:
        rules.append(
            f"If you are unsure whether a change violates a hard constraint ({', '.join(hard_ids[:3])}{'…' if len(hard_ids) > 3 else ''}), call repoctx.authority(task) — do not guess."
        )
    return rules


def before_finalize_checklist(
    *,
    validation_plan: ValidationPlan,
    edit_scope: EditScope,
    constraints: list[Constraint],
) -> list[str]:
    checklist = [
        "Call repoctx.validate_plan(task, changed_files) and run every command it returns.",
        "Call repoctx.risk_report(task, changed_files) and resolve every 'hard'-severity item.",
    ]
    if edit_scope.protected_paths:
        checklist.append("Verify no path in edit_scope.protected_paths was changed unintentionally.")
    if validation_plan.invariants_to_verify:
        checklist.append("Verify each invariant in validation_plan.invariants_to_verify.")
    if any(c.severity == "hard" for c in constraints):
        checklist.append("Re-read every hard constraint and confirm the diff does not violate it.")
    return checklist


def uncertainty_rule(constraints: list[Constraint]) -> str:
    if any(c.severity == "hard" for c in constraints):
        return (
            "If unsure whether a change violates a hard constraint, call repoctx.authority(task) "
            "instead of guessing. Never silently re-derive ground truth."
        )
    return (
        "If unsure about ground truth (contracts, invariants, protected flows), call repoctx.authority(task). "
        "Prefer asking repoctx over guessing."
    )
