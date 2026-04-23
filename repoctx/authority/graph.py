"""Authority graph: relationships between authority records and code files.

Edges modeled (design doc § 2.5):

- ``contract|invariant  --implemented_by--> file``
  file path matches an ``applies_to_paths`` glob of the authority record.
- ``invariant|contract  --enforced_by--> test``
  test path appears in ``validation_refs`` of a constraint, or imports an
  ``applies_to_paths`` match (import-graph check is delegated to the caller
  via the existing :class:`repoctx.models.DependencyGraph`).
- ``agent_instruction  --points_to--> authority_record``
  discovered via ``See contract: <path>`` markers resolved to an id.

Stored as two ``dict[str, set[str]]`` — same shape as :class:`DependencyGraph`
for ergonomic reuse.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Iterable

from repoctx.authority.records import AuthorityRecord

EdgeKind = str  # "implemented_by" | "enforced_by" | "points_to" | "validates"


@dataclass(slots=True)
class AuthorityGraph:
    # forward[edge_kind][source_id] = set of target ids
    forward: dict[EdgeKind, dict[str, set[str]]] = field(default_factory=dict)
    reverse: dict[EdgeKind, dict[str, set[str]]] = field(default_factory=dict)

    def add_edge(self, kind: EdgeKind, source: str, target: str) -> None:
        self.forward.setdefault(kind, {}).setdefault(source, set()).add(target)
        self.reverse.setdefault(kind, {}).setdefault(target, set()).add(source)

    def targets(self, kind: EdgeKind, source: str) -> set[str]:
        return self.forward.get(kind, {}).get(source, set())

    def sources(self, kind: EdgeKind, target: str) -> set[str]:
        return self.reverse.get(kind, {}).get(target, set())


def build_authority_graph(
    records: Iterable[AuthorityRecord],
    *,
    file_paths: Iterable[str],
    test_paths: Iterable[str] | None = None,
) -> AuthorityGraph:
    """Compute the authority graph from records and the set of repo files."""
    graph = AuthorityGraph()
    file_list = list(file_paths)
    test_set = set(test_paths or [])
    records_by_id = {r.id: r for r in records}
    records_by_path = {r.path.split(":", 1)[0]: r for r in records}

    for record in records_by_id.values():
        if record.type not in ("contract", "invariant"):
            continue
        for glob in record.applies_to_paths:
            for path in file_list:
                if fnmatch.fnmatch(path, glob):
                    graph.add_edge("implemented_by", record.id, path)
                    if path in test_set:
                        graph.add_edge("enforced_by", record.id, path)

    for record in records_by_id.values():
        if record.type != "agent_instruction":
            continue
        # "See contract: <path>" markers are themselves inline records; a
        # file-level agent_instruction doesn't carry those. We instead scan
        # for inline see_contract records that share the file path.
        ...

    # Inline "see_contract" records: point from source record's file to the target path.
    for record in records_by_id.values():
        if record.type == "architecture_note" and "inline" in record.tags and record.title.startswith("See contract:"):
            target_path = record.text.strip()
            target = records_by_path.get(target_path)
            if target is not None:
                graph.add_edge("points_to", record.id, target.id)

    return graph


__all__ = ["AuthorityGraph", "build_authority_graph"]
