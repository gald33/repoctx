"""Typed authority records and their mapping to the generic retrieval core.

An ``AuthorityRecord`` is a semantic wrapper around information that carries
*authority* in the repository (contracts, invariants, examples, agent
instructions, architecture notes, validation rules). It is *also* a
``RetrievableRecord`` consumer: :func:`authority_record_to_retrievable` emits
a ``RetrievableRecord`` whose ``record_type`` is the authority type and whose
metadata carries the :class:`AuthorityLevel`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Literal

from repoctx.record import RetrievableRecord

AUTHORITY_NAMESPACE = "authority"

AuthorityType = Literal[
    "contract",
    "invariant",
    "example",
    "agent_instruction",
    "architecture_note",
    "validation_rule",
]

AUTHORITY_RECORD_TYPES: tuple[AuthorityType, ...] = (
    "contract",
    "invariant",
    "example",
    "agent_instruction",
    "architecture_note",
    "validation_rule",
)


class AuthorityLevel(IntEnum):
    """Three-tier authority ordering. Lower values = higher authority."""

    HARD = 1           # contracts, invariants, do-not-change specs, golden tests
    GUIDED = 2         # AGENTS.md, architecture notes, ADRs
    IMPLEMENTATION = 3 # code, neighbors, ordinary tests


@dataclass(slots=True)
class AuthorityRecord:
    """A single typed authority artefact discovered in the repository."""

    id: str
    type: AuthorityType
    path: str
    title: str
    summary: str
    text: str
    authority_level: AuthorityLevel = AuthorityLevel.IMPLEMENTATION
    tags: list[str] = field(default_factory=list)
    related_ids: list[str] = field(default_factory=list)
    applies_to_paths: list[str] = field(default_factory=list)

    def excerpt(self, max_chars: int = 800) -> str:
        if len(self.text) <= max_chars:
            return self.text
        return self.text[: max_chars - 1].rstrip() + "…"


def authority_record_to_retrievable(record: AuthorityRecord) -> RetrievableRecord:
    """Convert an :class:`AuthorityRecord` into a generic retrievable record.

    The ``record_type`` is the authority type itself, so metadata filters like
    ``record_types=["contract", "invariant"]`` work directly against the core.
    """
    text_parts = [f"title: {record.title}", f"type: {record.type}"]
    if record.tags:
        text_parts.append("tags: " + ", ".join(record.tags))
    text_parts.append("")
    text_parts.append(record.text)
    embed_text = "\n".join(text_parts)

    metadata: dict[str, object] = {
        "path": record.path,
        "title": record.title,
        "summary": record.summary,
        "authority_level": int(record.authority_level),
        "authority_type": record.type,
        "tags": list(record.tags),
        "related_ids": list(record.related_ids),
        "applies_to_paths": list(record.applies_to_paths),
    }
    return RetrievableRecord(
        id=record.id,
        text=embed_text,
        record_type=record.type,
        namespace=AUTHORITY_NAMESPACE,
        metadata=metadata,
    )
