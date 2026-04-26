"""detect_changes(changed_files) — git diff → affected callers.

Borrowed from GitNexus's ``detect_changes`` tool. Maps a set of changed
files to the files that import or depend on them via the existing import
graph, so an agent can see "if I touch X, who else needs review?".

Symbol-level resolution would require a tree-sitter pass; we deliberately
stay file-level for now to match the rest of repoctx's footprint.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from repoctx.config import DEFAULT_CONFIG, RepoCtxConfig
from repoctx.git_state import collect_state
from repoctx.graph import build_dependency_graph
from repoctx.scanner import scan_repository


def op_detect_changes(
    changed_files: list[str],
    repo_root: str | Path = ".",
    config: RepoCtxConfig = DEFAULT_CONFIG,
    *,
    max_callers_per_file: int = 25,
) -> dict[str, Any]:
    """For each changed file, list its direct callers (reverse imports).

    If ``changed_files`` is empty, falls back to git's dirty file list so the
    tool is useful even when the agent hasn't tracked its own edits.
    """
    repo_path = Path(repo_root).resolve()

    if not changed_files:
        changed_files = list(collect_state(repo_path).get("dirty_files", []))

    index = scan_repository(repo_path, config=config)
    graph = build_dependency_graph(index)

    affected: list[dict[str, Any]] = []
    transitive: set[str] = set()

    for path in changed_files:
        direct_callers = sorted(graph.reverse.get(path, set()))
        # One hop of transitive expansion — enough to surface tests / wrappers
        # without exploding output.
        second_hop: set[str] = set()
        for caller in direct_callers:
            second_hop.update(graph.reverse.get(caller, set()))
        second_hop -= set(direct_callers)
        second_hop.discard(path)

        affected.append(
            {
                "changed": path,
                "in_index": path in index.records,
                "direct_callers": direct_callers[:max_callers_per_file],
                "indirect_callers": sorted(second_hop)[:max_callers_per_file],
                "imports": sorted(graph.forward.get(path, set()))[:max_callers_per_file],
            }
        )
        transitive.update(direct_callers)
        transitive.update(second_hop)

    return {
        "schema_version": "repoctx-bundle/1",
        "changed_files": list(changed_files),
        "affected": affected,
        "summary": {
            "changed_count": len(changed_files),
            "unique_callers": len(transitive - set(changed_files)),
        },
        "git_state": collect_state(repo_path),
    }


__all__ = ["op_detect_changes"]
