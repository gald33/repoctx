"""Per-repo feedback event log.

Append-only JSONL at ``<repo>/.repoctx/feedback-events.jsonl`` capturing signals
the Phase 3 tuner uses to fit per-kind retrieval thresholds. Distinct from the
*global* telemetry log at ``~/.repoctx/telemetry/`` ([telemetry.py](telemetry.py))
on two axes:

- **Location**: per-repo, so events stay co-located with the code they describe
  and the user can inspect / delete them per repo.
- **Content**: stores *unhashed* file paths and embedding scores so the tuner
  can join ``tool_use`` / ``self_report`` / ``git_edit`` events back to the
  ``bundle_emitted`` ranked-path list. The global telemetry log hashes
  task/repo and is therefore not a substitute.

Event types (discriminated by ``event_type``):

- ``bundle_emitted``: emitted on every successful ``op_bundle`` call. Carries
  ``ranked_paths`` (path/kind/score per ranked entry); the tuner joins later
  events to this via ``bundle_id``.
- ``tool_use``: emitted by the ``repoctx hook tool-use`` PostToolUse hook on
  Read/Edit/Write/MultiEdit. Attributes to the most-recent ``bundle_emitted``
  whose ``ranked_paths`` contains the touched path within an attribution
  window.
- ``self_report``: emitted by the ``mark_used`` MCP tool. Carries a graded
  relevance label (``informed_edit`` / ``informed_context`` / ``noise``).
- ``git_edit``: emitted by the reaper from ``git diff --name-only`` over the
  repo and any worktrees, for bundles that didn't get hook coverage.

All events carry a ``source`` field (``hook`` / ``git`` / ``self_report``) so
the tuner can weight them by provenance.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

FEEDBACK_DIR = ".repoctx"
FEEDBACK_FILENAME = "feedback-events.jsonl"
SCHEMA_VERSION = 1

# Default attribution window for tool_use events looking back for the
# bundle_emitted they belong to. Tuner-facing knobs are simple wall-clock and
# event-count caps — kept loose since the tuner can re-attribute at fit time.
ATTRIBUTION_WINDOW_SECONDS = 30 * 60
ATTRIBUTION_WINDOW_EVENTS = 200


def _feedback_path(repo_root: str | Path) -> Path:
    return Path(repo_root) / FEEDBACK_DIR / FEEDBACK_FILENAME


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def append_event(repo_root: str | Path, event: dict[str, Any]) -> Path | None:
    """Append a single event to the per-repo log.

    Honors the ``feedback_enabled`` opt-out from ``.repoctx/config.json``; when
    disabled, returns None without writing. Never raises on I/O errors — logs
    and swallows so a broken filesystem can't break retrieval. Adds default
    ``schema_version`` and ``event_time`` if not provided.
    """
    if not _is_enabled(repo_root):
        return None
    target = _feedback_path(repo_root)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(event)
        payload.setdefault("schema_version", SCHEMA_VERSION)
        payload.setdefault("event_time", _utc_now_iso())
        line = json.dumps(payload, sort_keys=True) + "\n"
        with target.open("a", encoding="utf-8") as handle:
            handle.write(line)
        return target
    except OSError as exc:
        logger.debug("Failed to append feedback event to %s: %s", target, exc)
        return None


def read_events(
    repo_root: str | Path,
    *,
    since_iso: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Stream events from the per-repo log in file order.

    ``since_iso`` filters by ``event_time`` (string compare works for ISO-8601
    in UTC, which is what we always write). Malformed lines are skipped with a
    warning — never raises.
    """
    target = _feedback_path(repo_root)
    if not target.exists():
        return
    try:
        with target.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    logger.warning("Skipping malformed feedback event: %s", exc)
                    continue
                if since_iso is not None:
                    et = payload.get("event_time")
                    if isinstance(et, str) and et < since_iso:
                        continue
                yield payload
    except OSError as exc:
        logger.debug("Failed to read feedback events from %s: %s", target, exc)
        return


def find_recent_bundle_for_path(
    repo_root: str | Path,
    path: str,
    *,
    now_iso: str | None = None,
    window_seconds: int = ATTRIBUTION_WINDOW_SECONDS,
    window_events: int = ATTRIBUTION_WINDOW_EVENTS,
) -> str | None:
    """Resolve the bundle_id a tool-use event should attribute to.

    Walks the log backward looking for the most-recent ``bundle_emitted`` event
    whose ``ranked_paths`` contains *path* — bounded by both an event-count
    window (last N events scanned) and a wall-clock window (events older than
    ``now - window_seconds`` are skipped). Returns None if no match.

    The window keeps attribution honest: a Read 50 turns or 30 minutes after
    a bundle is more likely the agent drifting to a new sub-task than feedback
    on the original retrieval.
    """
    events = list(read_events(repo_root))
    if not events:
        return None
    cutoff_iso = _cutoff_iso(now_iso, window_seconds)
    scanned = 0
    for evt in reversed(events):
        scanned += 1
        if scanned > window_events:
            break
        et = evt.get("event_time")
        if isinstance(et, str) and cutoff_iso and et < cutoff_iso:
            break
        if evt.get("event_type") != "bundle_emitted":
            continue
        ranked = evt.get("ranked_paths") or []
        for entry in ranked:
            if isinstance(entry, dict) and entry.get("path") == path:
                bid = evt.get("bundle_id")
                return bid if isinstance(bid, str) else None
    return None


def _cutoff_iso(now_iso: str | None, window_seconds: int) -> str | None:
    try:
        from datetime import timedelta
    except ImportError:
        return None
    if now_iso is None:
        now = datetime.now(timezone.utc)
    else:
        try:
            now = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
        except ValueError:
            return None
    cutoff = now - timedelta(seconds=window_seconds)
    return cutoff.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _is_enabled(repo_root: str | Path) -> bool:
    # Inline to avoid a circular import with config_loader (which itself reads
    # the same JSON file). Tolerates a missing/malformed config by defaulting
    # to enabled — the loader logs warnings; we don't double-log here.
    override = os.environ.get("REPOCTX_FEEDBACK_ENABLED")
    if override is not None:
        return override.lower() not in ("0", "false", "no", "off")
    cfg_path = Path(repo_root) / FEEDBACK_DIR / "config.json"
    if not cfg_path.exists():
        return True
    try:
        payload = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True
    if not isinstance(payload, dict):
        return True
    return bool(payload.get("feedback_enabled", True))
