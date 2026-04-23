"""GroundTruthBundle dataclasses and JSON serialization.

See ``docs/plans/2026-04-23-repoctx-v2-design.md`` § 3 for the schema spec.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from repoctx.authority.constraints import Constraint
from repoctx.authority.records import AuthorityRecord

BUNDLE_SCHEMA_VERSION = "repoctx-bundle/1"


@dataclass(slots=True)
class EditScope:
    allowed_paths: list[str] = field(default_factory=list)
    related_paths: list[str] = field(default_factory=list)
    protected_paths: list[str] = field(default_factory=list)
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_paths": list(self.allowed_paths),
            "related_paths": list(self.related_paths),
            "protected_paths": list(self.protected_paths),
            "rationale": self.rationale,
        }


@dataclass(slots=True)
class ValidationPlan:
    commands: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    contract_checks: list[str] = field(default_factory=list)
    invariants_to_verify: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "commands": list(self.commands),
            "tests": list(self.tests),
            "contract_checks": list(self.contract_checks),
            "invariants_to_verify": list(self.invariants_to_verify),
        }


Severity = Literal["hard", "guided", "advisory"]


@dataclass(slots=True)
class RiskNote:
    risk: str
    why: str
    severity: Severity = "guided"
    related_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk": self.risk,
            "why": self.why,
            "severity": self.severity,
            "related_ids": list(self.related_ids),
        }


@dataclass(slots=True)
class RankedCodeRef:
    path: str
    reason: str
    score: float = 0.0
    snippet: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"path": self.path, "reason": self.reason, "score": self.score}
        if self.snippet is not None:
            data["snippet"] = self.snippet
        return data


@dataclass(slots=True)
class GroundTruthBundle:
    task_summary: str
    task_raw: str
    authoritative_records: list[AuthorityRecord] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)
    relevant_code: list[RankedCodeRef] = field(default_factory=list)
    examples: list[RankedCodeRef] = field(default_factory=list)
    edit_scope: EditScope = field(default_factory=EditScope)
    validation_plan: ValidationPlan = field(default_factory=ValidationPlan)
    risk_notes: list[RiskNote] = field(default_factory=list)
    when_to_recall_repoctx: list[str] = field(default_factory=list)
    before_finalize_checklist: list[str] = field(default_factory=list)
    uncertainty_rule: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, include_full_text: bool = False) -> dict[str, Any]:
        def _auth_to_dict(r: AuthorityRecord) -> dict[str, Any]:
            return {
                "id": r.id,
                "type": r.type,
                "authority_level": int(r.authority_level),
                "path": r.path,
                "title": r.title,
                "summary": r.summary,
                "excerpt": r.text if include_full_text else r.excerpt(),
                "tags": list(r.tags),
                "related_ids": list(r.related_ids),
            }

        return {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "task": {"summary": self.task_summary, "raw": self.task_raw},
            "authority": {
                "records": [_auth_to_dict(r) for r in self.authoritative_records],
                "constraints": [c.to_dict() for c in self.constraints],
            },
            "relevant_code": [r.to_dict() for r in self.relevant_code],
            "examples": [r.to_dict() for r in self.examples],
            "edit_scope": self.edit_scope.to_dict(),
            "validation_plan": self.validation_plan.to_dict(),
            "risk_notes": [r.to_dict() for r in self.risk_notes],
            "when_to_recall_repoctx": list(self.when_to_recall_repoctx),
            "before_finalize_checklist": list(self.before_finalize_checklist),
            "uncertainty_rule": self.uncertainty_rule,
            "metrics": dict(self.metrics),
        }
