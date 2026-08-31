"""Shared origin/main staleness surfacing for read-path protocol ops.

Every op that *reads* the embedding index owes the caller one thing: if the
index is behind origin/main, say so. Otherwise a stale index is
indistinguishable from a fresh one — the results still look confident and
well-formed, they're just answering from an old snapshot of the repo.

That failure actually happened. `bundle` and `semantic_search` surfaced the
warning from the start, but `scope`, `risk_report` and `validate_plan` did
not, so a repo whose index had frozen 1145 files behind kept serving stale
retrieval for two months without a single visible signal.

Both helpers are best-effort and never raise: a staleness *warning* must never
be the reason a tool call fails.

Wire any new read-path op through here rather than re-implementing it.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def refresh_base(repo_root: str | Path) -> dict | None:
    """TTL-gated origin/main refresh for the read path; None if unavailable.

    Returns the status dict from :func:`maybe_refresh_base_on_read` (which
    re-embeds a small delta and only *reports* a large one).
    """
    # Bail on a path that isn't a real directory. The refresh writes a
    # fetch-TTL stamp under ``<repo>/.repoctx/state``, so probing a bad path
    # would *create* it — turning a caller's typo into a stray directory and
    # masking the "no such repo" error the caller should have surfaced.
    try:
        if not Path(repo_root).is_dir():
            return None
    except OSError:
        return None
    try:
        from repoctx.embeddings import maybe_refresh_base_on_read
    except ImportError:
        return None
    try:
        return maybe_refresh_base_on_read(repo_root)
    except Exception:
        logger.debug("base refresh failed", exc_info=True)
        return None


def attach_base_status(payload: dict[str, Any], base_status: dict | None) -> None:
    """Record *base_status* on *payload* and append a warning when stale.

    Mirrors the shape `bundle` already emits: the raw status under
    ``retrieval.base`` for machine consumers, and a human-readable line
    appended to ``warnings`` so an agent relaying the payload can't miss it.
    """
    if not base_status:
        return
    try:
        from repoctx.embeddings import base_staleness_warning

        payload.setdefault("retrieval", {})["base"] = base_status
        warning = base_staleness_warning(base_status)
        if warning:
            payload.setdefault("warnings", []).append(warning)
    except Exception:
        logger.debug("attach base status failed", exc_info=True)


__all__ = ["refresh_base", "attach_base_status"]
