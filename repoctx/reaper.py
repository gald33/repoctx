"""Git-diff feedback reaper.

Closes the loop for the universal-fallback signal: in environments where the
PostToolUse hook isn't installed (Cursor, Codex, plain MCP clients), the only
observable feedback is what made it into the working tree. ``reap`` enumerates
every worktree (main + ``git worktree list``) and emits ``git_edit`` events
for any modified file that appears in a recently-emitted bundle's
``ranked_paths``.

Triggered from three places:

- ``Stop`` hook handler ([hooks.py](hooks.py)) — once per turn end.
- Lazy run inside ``op_bundle`` — catches sessions that died without a
  clean Stop.
- ``repoctx reap`` CLI — manual catch-up.

Idempotent: tracks per-bundle ``last_reaped_at`` so re-runs don't duplicate
events. Worktree-aware: a worktree that's been cleaned up before the reaper
runs leaves no git signal, but the hook source already covered that case
in Claude Code; the worktree gap only affects non-hook environments.
"""

from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from repoctx.feedback_log import append_event, read_events

logger = logging.getLogger(__name__)


def reap(repo_root: str | Path, *, now_iso: str | None = None) -> dict[str, Any]:
    """Reconcile open bundles against current git state.

    Returns a summary dict: ``{"bundles_scanned", "edits_emitted",
    "worktrees_checked"}``. Always succeeds — git errors are logged and
    treated as "no edits observed" for that worktree.
    """
    repo = Path(repo_root).resolve()
    open_bundles = _open_bundles(repo)
    if not open_bundles:
        return {"bundles_scanned": 0, "edits_emitted": 0, "worktrees_checked": 0}

    worktrees = _list_worktrees(repo)
    edited_paths_per_worktree: dict[Path, set[str]] = {}
    for wt in worktrees:
        edited_paths_per_worktree[wt] = _changed_paths(wt)

    emitted = 0
    now_str = now_iso or _utc_now_iso()
    for bundle_id, entry in open_bundles.items():
        bundle_paths: set[str] = entry["paths"]
        already_emitted: set[str] = entry["already_emitted"]
        for wt, edited in edited_paths_per_worktree.items():
            # A path in the bundle that shows as modified in any worktree
            # counts as a git_edit. We attribute to the main repo path for
            # consistency; the worktree origin is stashed in the event for
            # forensics.
            for rel in edited & bundle_paths:
                if rel in already_emitted:
                    continue
                append_event(
                    repo,
                    {
                        "event_type": "git_edit",
                        "bundle_id": bundle_id,
                        "path": rel,
                        "action": "Edit",
                        "source": "git",
                        "worktree": str(wt),
                        "repo_root": str(repo),
                    },
                )
                emitted += 1
                already_emitted.add(rel)

        # Mark this bundle reaped (no-op data, used to bound future scans).
        append_event(
            repo,
            {
                "event_type": "bundle_reaped",
                "bundle_id": bundle_id,
                "source": "git",
                "repo_root": str(repo),
                "reaped_at": now_str,
            },
        )

    return {
        "bundles_scanned": len(open_bundles),
        "edits_emitted": emitted,
        "worktrees_checked": len(worktrees),
    }


def _open_bundles(repo_root: Path) -> dict[str, dict[str, Any]]:
    """Index every bundle_emitted event that hasn't yet been reaped.

    Each entry carries the set of ranked-path strings and the set of paths
    that already have a git_edit event recorded for this bundle (so re-runs
    don't duplicate).
    """
    bundles: dict[str, dict[str, Any]] = {}
    reaped_ids: set[str] = set()
    for evt in read_events(repo_root):
        bid = evt.get("bundle_id")
        if not isinstance(bid, str) or not bid:
            continue
        et = evt.get("event_type")
        if et == "bundle_emitted":
            ranked = evt.get("ranked_paths") or []
            paths: set[str] = set()
            for entry in ranked:
                if isinstance(entry, dict):
                    p = entry.get("path")
                    if isinstance(p, str):
                        paths.add(p)
            bundles[bid] = {"paths": paths, "already_emitted": set()}
        elif et == "git_edit":
            entry = bundles.get(bid)
            p = evt.get("path")
            if entry and isinstance(p, str):
                entry["already_emitted"].add(p)
        elif et == "bundle_reaped":
            reaped_ids.add(bid)
    # A reaped bundle stays in the dict (we may want to top-up later if new
    # edits arrive against the same paths), but if its bundle_emitted is
    # older than the reaped marker and no new emit events exist, we treat
    # it as closed for this run. Simple policy: drop bundles whose only
    # reaped marker has no subsequent git_edit. For Phase 1 simplicity, we
    # just allow re-reaping (the already_emitted set protects against
    # duplicates anyway).
    return bundles


def _list_worktrees(repo_root: Path) -> list[Path]:
    """Enumerate the main repo + any linked worktrees, best-effort.

    Falls back to ``[repo_root]`` if git isn't available or the repo isn't
    a git checkout — the reaper still does useful work in that case via
    the main path.
    """
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.debug("git worktree list failed: %s", exc)
        return [repo_root]
    if result.returncode != 0:
        logger.debug("git worktree list returned %d: %s", result.returncode, result.stderr.strip())
        return [repo_root]

    worktrees: list[Path] = []
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            wt_path = line[len("worktree "):].strip()
            if wt_path:
                p = Path(wt_path)
                if p.exists():
                    worktrees.append(p.resolve())
    if not worktrees:
        worktrees.append(repo_root)
    return worktrees


def _changed_paths(worktree: Path) -> set[str]:
    """Return repo-relative paths with changes vs. HEAD in *worktree*.

    Includes both unstaged and staged changes, and untracked files (so a
    fresh edit doesn't get missed). Returns an empty set on any git error.
    """
    paths: set[str] = set()
    try:
        # Modified + staged (vs HEAD)
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=str(worktree),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                line = line.strip()
                if line:
                    paths.add(line)
        # Untracked
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=str(worktree),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if untracked.returncode == 0:
            for line in untracked.stdout.splitlines():
                line = line.strip()
                if line:
                    paths.add(line)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.debug("git diff/ls-files failed in %s: %s", worktree, exc)
    return paths


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
