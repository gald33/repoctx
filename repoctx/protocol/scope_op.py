"""scope(task) — edit-scope decision support."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from repoctx.bundle import build_bundle


def op_scope(task: str, repo_root: str | Path = ".") -> dict[str, Any]:
    bundle = build_bundle(task, repo_root=repo_root)
    return {
        "schema_version": "repoctx-bundle/1",
        "task": {"summary": bundle.task_summary, "raw": bundle.task_raw},
        "edit_scope": bundle.edit_scope.to_dict(),
        "when_to_recall_repoctx": list(bundle.when_to_recall_repoctx),
        "staleness": dict(bundle.staleness),
    }
