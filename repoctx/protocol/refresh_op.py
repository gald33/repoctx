"""refresh(task, changed_files, current_scope) — incremental bundle update."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from repoctx.bundle import build_bundle
from repoctx.protocol.changes_op import op_detect_changes


def op_refresh(
    task: str,
    changed_files: list[str],
    current_scope: dict[str, Any] | None = None,
    repo_root: str | Path = ".",
) -> dict[str, Any]:
    bundle = build_bundle(task, repo_root=repo_root)
    new_scope = bundle.edit_scope.to_dict()
    changes = op_detect_changes(changed_files, repo_root=repo_root) if changed_files else None

    def _diff(key: str) -> list[str]:
        old = set((current_scope or {}).get(key, []))
        new = set(new_scope.get(key, []))
        return sorted(new - old)

    return {
        "schema_version": "repoctx-bundle/1",
        "task": {"summary": bundle.task_summary, "raw": bundle.task_raw},
        "changed_files": list(changed_files),
        "edit_scope": new_scope,
        "scope_delta": {
            "added_allowed_paths": _diff("allowed_paths"),
            "added_related_paths": _diff("related_paths"),
            "added_protected_paths": _diff("protected_paths"),
        },
        "added_authority": [
            {"id": r.id, "type": r.type, "title": r.title, "authority_level": int(r.authority_level)}
            for r in bundle.authoritative_records
        ],
        "affected": (changes or {}).get("affected", []),
        "staleness": dict(bundle.staleness),
        "when_to_recall_repoctx": list(bundle.when_to_recall_repoctx),
    }
