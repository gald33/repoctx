"""``repoctx tune`` — fit per-kind retrieval thresholds from the feedback log.

Phase 3 of the per-repo retrieval-tuning loop. With ``--dry-run`` (default),
prints the fit without writing. Pass ``--apply`` to write the fitted
thresholds into ``.repoctx/config.json`` under a ``learned`` block.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace


def _register(subparsers) -> None:
    p = subparsers.add_parser(
        "tune",
        help="Fit per-kind retrieval thresholds from the feedback log",
        description=(
            "Reads <repo>/.repoctx/feedback-events.jsonl, fits a per-kind "
            "qualify-threshold with a strong prior on the configured default "
            "(σ=0.07), and writes the result to .repoctx/config.json's "
            "`learned` block. Run after collecting ≥10 labels per kind."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--repo", default=".", help="Repository root")
    p.add_argument(
        "--apply",
        action="store_true",
        help="Write fitted thresholds to .repoctx/config.json (default: dry-run)",
    )
    p.add_argument("--json", action="store_true", help="Emit JSON instead of a table")
    p.add_argument(
        "--half-life-days",
        type=float,
        default=None,
        help="Half-life for label time-decay (default: 30 days)",
    )
    p.add_argument(
        "--prior-sigma",
        type=float,
        default=None,
        help="Std-dev of the Gaussian prior over thresholds (default: 0.07)",
    )
    p.add_argument(
        "--min-labels",
        type=int,
        default=None,
        help="Minimum labels per kind before fitting (default: 10)",
    )


def _run(args: argparse.Namespace) -> None:
    from repoctx.tune import TuneConfig, apply_tune, tune

    kwargs: dict[str, object] = {}
    if args.half_life_days is not None:
        kwargs["half_life_days"] = args.half_life_days
    if args.prior_sigma is not None:
        kwargs["prior_sigma"] = args.prior_sigma
    if args.min_labels is not None:
        kwargs["min_labels_per_kind"] = args.min_labels
    cfg = TuneConfig(**kwargs) if kwargs else None

    result = tune(Path(args.repo), config=cfg)

    if args.json:
        out = result.to_dict()
        if args.apply:
            path = apply_tune(Path(args.repo), result)
            out["applied_to"] = str(path)
        print(json.dumps(out, indent=2))
        return

    print(f"{'kind':<10}{'labels':>8}{'pos_w':>8}{'noise_w':>8}{'prior':>8}{'fitted':>8}  confidence")
    print("-" * 70)
    for f in result.fits:
        arrow = "→" if abs(f.fitted_threshold - f.prior_threshold) > 1e-6 else "="
        print(
            f"{f.kind:<10}{f.label_count:>8}{f.positive_weight:>8.2f}"
            f"{f.noise_weight:>8.2f}{f.prior_threshold:>8.3f}{f.fitted_threshold:>8.3f}"
            f"  {arrow} {f.confidence}"
        )
    if args.apply:
        path = apply_tune(Path(args.repo), result)
        print(f"\nApplied → {path}")
    else:
        print("\n(dry-run; pass --apply to write to .repoctx/config.json)")


tune_cmd = SimpleNamespace(NAME="tune", register=_register, run=_run)
