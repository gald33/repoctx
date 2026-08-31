"""scope(task) — edit-scope decision support."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from repoctx.bundle import build_bundle
from repoctx.protocol.base_status import attach_base_status, refresh_base


def op_scope(task: str, repo_root: str | Path = ".") -> dict[str, Any]:
    try:
        from repoctx.embeddings import maybe_flush_on_read
        maybe_flush_on_read(repo_root=repo_root)
    except ImportError:
        pass
    base_status = refresh_base(repo_root)
    bundle = build_bundle(task, repo_root=repo_root)
    payload = {
        "schema_version": "repoctx-bundle/1",
        "task": {"summary": bundle.task_summary, "raw": bundle.task_raw},
        "edit_scope": bundle.edit_scope.to_dict(),
        "when_to_recall_repoctx": list(bundle.when_to_recall_repoctx),
        "staleness": dict(bundle.staleness),
    }
    attach_base_status(payload, base_status)
    return payload
