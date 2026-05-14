"""``repoctx reap`` — manual git-diff reaper run.

Same as the Stop-hook / pre-bundle reap, but invoked explicitly. Useful when
working in IDEs without PostToolUse hooks: run after a coding session to
attribute git-observed edits to the bundles that surfaced them.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace


def _register(subparsers) -> None:
    p = subparsers.add_parser(
        "reap",
        help="Reconcile open bundles against current git state",
        description=(
            "Walk every worktree, find files modified vs HEAD that appear in "
            "recently-emitted bundles, and emit git_edit feedback events for "
            "them. Idempotent."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--repo", default=".", help="Repository root")
    p.add_argument("--json", action="store_true", help="Print summary as JSON")


def _run(args: argparse.Namespace) -> None:
    from repoctx.reaper import reap

    summary = reap(Path(args.repo))
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(
            f"Scanned {summary['bundles_scanned']} open bundle(s) across "
            f"{summary['worktrees_checked']} worktree(s); "
            f"emitted {summary['edits_emitted']} git_edit event(s)."
        )


reap_cmd = SimpleNamespace(NAME="reap", register=_register, run=_run)
