"""First-class constraint objects extracted from authority records."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Literal

Severity = Literal["hard", "guided", "advisory"]
Scope = Literal["global", "path", "module", "subsystem"]


@dataclass(slots=True)
class Constraint:
    id: str
    statement: str
    source_record_id: str
    scope: Scope = "path"
    applies_to_paths: list[str] = field(default_factory=list)
    severity: Severity = "guided"
    validation_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "statement": self.statement,
            "source_record_id": self.source_record_id,
            "scope": self.scope,
            "applies_to_paths": list(self.applies_to_paths),
            "severity": self.severity,
            "validation_refs": list(self.validation_refs),
        }


def constraint_id(source_record_id: str, statement: str) -> str:
    digest = hashlib.sha256(f"{source_record_id}|{statement}".encode("utf-8")).hexdigest()
    return f"const:{digest[:12]}"
