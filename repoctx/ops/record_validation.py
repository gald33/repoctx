"""``record_validation`` op — did the validation plan actually catch anything?

Everything else repoctx measures about ``validate_plan`` is invocation:
`protocol_op` records that the op ran, how long it took, and whether *repoctx
itself* raised. Across 322 recorded validate_plan/risk_report events, all 14
failures were repoctx crashing (IndexError, FileNotFoundError). Not one was a
test failing, because no event has ever carried a test result.

That left the feedback loop closed for retrieval and open for validation.
Retrieval gets graded per path by ``mark_used`` and tuned; validation only got
counted. A ``validate_plan`` that returns a confident list of irrelevant tests
was indistinguishable from one that caught a real regression.

This op closes it. The agent reports the exit status of the commands the plan
returned, joined to the bundle by ``bundle_id`` exactly as ``mark_used`` is, so
``repoctx eval`` can answer two questions it previously could not:

* **coverage** — of bundles that shipped a validation plan, how many had it run?
* **catch rate** — of validation runs, how many failed?

A *failing* run is the useful one: it is validation catching something before
the agent declared done. A run that never happens is the case this whole
mechanism exists to prevent, and until now it looked identical to a pass.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from repoctx.feedback_log import append_event

logger = logging.getLogger(__name__)

#: Truncate a recorded command so a pathological one-liner can't bloat the log.
MAX_COMMAND_CHARS = 500


def op_record_validation(
    bundle_id: str,
    runs: list[dict[str, Any]],
    *,
    repo_root: str | Path,
) -> dict[str, Any]:
    """Append one ``validation_run`` event per entry in *runs*.

    *runs* is a list of ``{"command": str, "exit_code": int}`` dicts — the
    commands from the bundle's ``validation_plan`` and what they returned.
    ``passed`` is derived from ``exit_code == 0`` rather than trusted from the
    caller, so a self-report cannot claim a pass it did not get.

    Invalid entries are skipped with a warning rather than rejecting the whole
    payload, matching ``mark_used``: a partially-bad report should still record
    its good rows.

    Returns ``{"recorded", "skipped", "passed", "failed", "bundle_id"}``. Never
    raises on I/O errors — feedback logging must never break the agent's task.
    """
    if not isinstance(bundle_id, str) or not bundle_id:
        return {"recorded": 0, "skipped": 0, "passed": 0, "failed": 0,
                "bundle_id": "", "error": "bundle_id required"}
    if not isinstance(runs, list):
        return {"recorded": 0, "skipped": 0, "passed": 0, "failed": 0,
                "bundle_id": bundle_id, "error": "runs must be a list"}

    recorded = passed = failed = skipped = 0
    for entry in runs:
        if not isinstance(entry, dict):
            skipped += 1
            logger.warning("record_validation: skipping non-object entry: %r", entry)
            continue
        command = entry.get("command")
        exit_code = entry.get("exit_code")
        if not isinstance(command, str) or not command.strip():
            skipped += 1
            logger.warning("record_validation: skipping entry with missing/empty command")
            continue
        # bool is a subclass of int; an exit code of True is a caller mistake.
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            skipped += 1
            logger.warning(
                "record_validation: skipping %r — exit_code must be an int, got %r",
                command[:60], exit_code,
            )
            continue
        ok = exit_code == 0
        append_event(
            repo_root,
            {
                "event_type": "validation_run",
                "bundle_id": bundle_id,
                "command": command.strip()[:MAX_COMMAND_CHARS],
                "exit_code": exit_code,
                "passed": ok,
                "source": "self_report",
                "repo_root": str(Path(repo_root).resolve()),
            },
        )
        recorded += 1
        passed += ok
        failed += not ok

    return {
        "recorded": recorded,
        "skipped": skipped,
        "passed": passed,
        "failed": failed,
        "bundle_id": bundle_id,
    }
