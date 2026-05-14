"""``mark_used`` op — LLM-judged graded relevance labels for a bundle.

This is the only feedback source that captures "I read file A and it informed
my edit of file B" — the hook source sees Reads but can't attribute their
context-contribution, and git-diff only sees Edits. The LLM is the sole judge
that can mark a non-edited Read as ``informed_context`` (the prize signal) or
a path that ended up in the bundle as ``noise`` (high-confidence negative).

The tuner in Phase 3 weights these labels heavily for non-edited Reads and
for explicit ``noise``, and lightly for ``informed_edit`` (which the hook /
git sources observe directly and with less self-attribution bias).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from repoctx.feedback_log import append_event

logger = logging.getLogger(__name__)

VALID_RELEVANCE = ("informed_edit", "informed_context", "noise")


def op_mark_used(
    bundle_id: str,
    labels: list[dict[str, Any]],
    *,
    repo_root: str | Path,
) -> dict[str, Any]:
    """Append a self-report label per entry in *labels*.

    *labels* is a list of ``{"path": str, "relevance": "informed_edit" |
    "informed_context" | "noise"}`` dicts. Invalid entries are skipped with
    a logged warning rather than rejected wholesale so a partially-bad
    payload still records the good labels.

    Returns ``{"recorded": N, "skipped": M, "bundle_id": <id>}`` so the
    agent gets confirmation. Never raises on I/O errors — feedback logging
    must never break the agent's task.
    """
    if not isinstance(bundle_id, str) or not bundle_id:
        return {"recorded": 0, "skipped": 0, "bundle_id": "", "error": "bundle_id required"}
    if not isinstance(labels, list):
        return {"recorded": 0, "skipped": 0, "bundle_id": bundle_id, "error": "labels must be a list"}

    recorded = 0
    skipped = 0
    for entry in labels:
        if not isinstance(entry, dict):
            skipped += 1
            logger.warning("mark_used: skipping non-object label entry: %r", entry)
            continue
        path = entry.get("path")
        relevance = entry.get("relevance")
        if not isinstance(path, str) or not path.strip():
            skipped += 1
            logger.warning("mark_used: skipping entry with missing/empty path")
            continue
        if relevance not in VALID_RELEVANCE:
            skipped += 1
            logger.warning(
                "mark_used: skipping entry with invalid relevance %r (expected one of %s)",
                relevance, VALID_RELEVANCE,
            )
            continue
        append_event(
            repo_root,
            {
                "event_type": "self_report",
                "bundle_id": bundle_id,
                "path": path,
                "relevance": relevance,
                "source": "self_report",
                "repo_root": str(Path(repo_root).resolve()),
            },
        )
        recorded += 1

    return {"recorded": recorded, "skipped": skipped, "bundle_id": bundle_id}
