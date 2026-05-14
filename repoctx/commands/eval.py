"""``repoctx eval`` — compute precision/recall/noise from the feedback log.

Phase 2 of the per-repo retrieval-tuning loop. Doesn't change retrieval —
just makes the effect of any tuning change measurable. Run after collecting
a few weeks of feedback events.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace


def _register(subparsers) -> None:
    p = subparsers.add_parser(
        "eval",
        help="Aggregate feedback events into per-kind precision/recall/noise",
        description=(
            "Joins bundle_emitted events with tool_use, self_report, and "
            "git_edit events to compute, per kind, how often shipped paths "
            "earned a positive label (precision), how often touched paths "
            "were in the bundle (recall), and the explicit noise rate."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--repo", default=".", help="Repository root")
    p.add_argument(
        "--since",
        default=None,
        help="ISO-8601 cutoff (UTC, e.g. 2026-04-01T00:00:00Z). Events older are ignored.",
    )
    p.add_argument("--json", action="store_true", help="Emit JSON instead of a formatted table")


def _run(args: argparse.Namespace) -> None:
    from repoctx.eval import compute_eval

    report = compute_eval(Path(args.repo), since_iso=args.since)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
        return

    print(f"Bundles: {report.bundles}")
    print(f"Events:  {report.events_total}")
    print()
    print(f"{'kind':<10}{'bundle':>8}{'pos':>6}{'noise':>6}{'miss':>6}{'prec':>8}{'recall':>8}{'noise%':>8}")
    print("-" * 60)
    for kind, stats in sorted(report.by_kind.items()):
        print(
            f"{kind:<10}{stats.bundle_paths:>8}{stats.positives:>6}"
            f"{stats.noise:>6}{stats.misses:>6}"
            f"{stats.precision():>8.3f}{stats.recall():>8.3f}{stats.noise_rate() * 100:>7.1f}%"
        )
    print("-" * 60)
    o = report.overall
    print(
        f"{'overall':<10}{o.bundle_paths:>8}{o.positives:>6}{o.noise:>6}{o.misses:>6}"
        f"{o.precision():>8.3f}{o.recall():>8.3f}{o.noise_rate() * 100:>7.1f}%"
    )


eval_cmd = SimpleNamespace(NAME="eval", register=_register, run=_run)
