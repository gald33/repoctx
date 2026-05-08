import argparse
import json
from pathlib import Path

NAME = "stats"


def register(subparsers) -> None:
    st = subparsers.add_parser(
        NAME,
        help="Aggregate telemetry: per-op counts, success rate, p50/p95 latency",
    )
    st.add_argument(
        "--days",
        type=int,
        default=30,
        help="Window in days (default 30; pass 0 for all time)",
    )
    st.add_argument(
        "--repo",
        default=None,
        help="Filter to a specific repo (path; will be hashed for matching)",
    )
    st.add_argument(
        "--format",
        choices=["json", "markdown"],
        default="markdown",
        help="Output format (default markdown)",
    )


def run(args: argparse.Namespace) -> None:
    from repoctx.stats import compute_stats, render_markdown
    from repoctx.telemetry import sha256_hex

    days = None if args.days == 0 else args.days
    repo_hash = None
    if args.repo:
        repo_hash = sha256_hex(str(Path(args.repo).resolve()))
    stats = compute_stats(days=days, repo_hash=repo_hash)
    if args.format == "json":
        print(json.dumps(stats, indent=2))
    else:
        print(render_markdown(stats))
