"""Aggregate repoctx telemetry into a digest.

Telemetry is already written per-call to ``~/.repoctx/telemetry/repoctx-events.jsonl``
by ``record_protocol_op`` and ``record_repoctx_invocation``. This module reads
those events and produces:

- per-op counts, success rates, p50/p95 latency
- output-size summary
- daily activity histogram
- top-N repos (hashed) and recent error types

Privacy-preserving by construction — query/task strings and repo paths are
already SHA-256 hashed at write time. We never read or surface the raw
content; only the hashes pass through.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any

from repoctx.telemetry import (
    REPOCTX_EVENTS_FILE,
    _read_jsonl,
    get_telemetry_dir,
)


def compute_stats(
    *,
    telemetry_dir: str | Path | None = None,
    days: int | None = 30,
    repo_hash: str | None = None,
) -> dict[str, Any]:
    """Read telemetry events and produce an aggregate digest.

    ``days``: limit to events in the last N days (None = no time filter).
    ``repo_hash``: if set, only include events for that repo hash (callers
    can compute it via ``sha256_hex(str(repo_root.resolve()))``).
    """
    events = _read_jsonl(telemetry_dir, REPOCTX_EVENTS_FILE)

    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        events = [e for e in events if _parse_time(e.get("event_time")) >= cutoff]
    if repo_hash:
        events = [e for e in events if e.get("repo_hash") == repo_hash]

    by_op: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        op = e.get("op") or e.get("event_type") or "unknown"
        by_op[op].append(e)

    op_summary: list[dict[str, Any]] = []
    for op, op_events in sorted(by_op.items()):
        durations = [
            int(e.get("duration_ms") or e.get("repoctx_duration_ms") or 0)
            for e in op_events
        ]
        durations = [d for d in durations if d > 0]
        successes = sum(1 for e in op_events if e.get("success"))
        bytes_out = [int(e.get("output_bytes") or 0) for e in op_events]
        op_summary.append(
            {
                "op": op,
                "count": len(op_events),
                "success_count": successes,
                "success_rate": round(successes / len(op_events), 3) if op_events else 0.0,
                "duration_ms": _percentiles(durations),
                "output_bytes": _percentiles(bytes_out),
            }
        )

    # Daily activity histogram, oldest -> newest.
    by_day: Counter[str] = Counter()
    for e in events:
        ts = _parse_time(e.get("event_time"))
        if ts == datetime.min.replace(tzinfo=timezone.utc):
            continue
        by_day[ts.date().isoformat()] += 1

    daily = [{"date": d, "count": c} for d, c in sorted(by_day.items())]

    # Top repos and surfaces.
    top_repos = Counter(e.get("repo_hash") for e in events if e.get("repo_hash"))
    surfaces = Counter(e.get("surface") for e in events if e.get("surface"))

    # Recent errors (most recent N).
    recent_errors: list[dict[str, Any]] = []
    for e in reversed(events):
        if not e.get("success") and e.get("error_type"):
            recent_errors.append(
                {
                    "event_time": e.get("event_time"),
                    "op": e.get("op") or e.get("event_type"),
                    "error_type": e.get("error_type"),
                }
            )
            if len(recent_errors) >= 10:
                break

    return {
        "schema_version": "repoctx-stats/1",
        "telemetry_dir": str(get_telemetry_dir(telemetry_dir)),
        "window_days": days,
        "total_events": len(events),
        "by_op": op_summary,
        "daily_activity": daily,
        "top_repos": [
            {"repo_hash": h, "count": c}
            for h, c in top_repos.most_common(5)
        ],
        "surface_breakdown": [
            {"surface": s, "count": c} for s, c in surfaces.most_common()
        ],
        "recent_errors": recent_errors,
    }


def render_markdown(stats: dict[str, Any]) -> str:
    """Human-readable digest. CLI-friendly."""
    lines: list[str] = []
    lines.append("# repoctx stats\n")
    window = stats.get("window_days")
    window_str = f"last {window} days" if window else "all time"
    lines.append(f"_{window_str} — {stats['total_events']} events from `{stats['telemetry_dir']}`_\n")

    if not stats["by_op"]:
        lines.append("No events recorded yet. Use repoctx (CLI or MCP) and rerun.\n")
        return "\n".join(lines)

    lines.append("## Per-op summary\n")
    lines.append("| Op | Calls | Success rate | p50 ms | p95 ms | p50 bytes |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for row in stats["by_op"]:
        d = row["duration_ms"]
        b = row["output_bytes"]
        lines.append(
            f"| `{row['op']}` | {row['count']} | {row['success_rate']:.0%} "
            f"| {d['p50']} | {d['p95']} | {b['p50']} |"
        )
    lines.append("")

    if stats["surface_breakdown"]:
        lines.append("## By surface\n")
        for s in stats["surface_breakdown"]:
            lines.append(f"- `{s['surface']}`: {s['count']}")
        lines.append("")

    if stats["daily_activity"]:
        lines.append("## Daily activity\n")
        max_count = max(d["count"] for d in stats["daily_activity"])
        for d in stats["daily_activity"][-14:]:
            bar = "█" * max(1, int(d["count"] / max_count * 30))
            lines.append(f"- `{d['date']}` {bar} {d['count']}")
        lines.append("")

    if stats["recent_errors"]:
        lines.append("## Recent errors\n")
        for err in stats["recent_errors"]:
            lines.append(f"- `{err['event_time']}` `{err['op']}` → `{err['error_type']}`")
        lines.append("")

    return "\n".join(lines)


# ---- internals ---------------------------------------------------------------


def _percentiles(values: list[int]) -> dict[str, int]:
    if not values:
        return {"p50": 0, "p95": 0, "max": 0, "n": 0}
    sorted_vals = sorted(values)
    p50 = int(median(sorted_vals))
    idx_p95 = max(0, int(len(sorted_vals) * 0.95) - 1)
    p95 = sorted_vals[idx_p95]
    return {"p50": p50, "p95": p95, "max": sorted_vals[-1], "n": len(sorted_vals)}


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str):
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        # Stored as 'YYYY-MM-DDTHH:MM:SSZ'
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


__all__ = ["compute_stats", "render_markdown"]


def main() -> None:
    """``python -m repoctx.stats`` entrypoint, primarily for debugging."""
    import argparse

    parser = argparse.ArgumentParser(description="Aggregate repoctx telemetry")
    parser.add_argument("--days", type=int, default=30, help="Window in days (default 30, 0 = all)")
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    args = parser.parse_args()
    days = None if args.days == 0 else args.days
    stats = compute_stats(days=days)
    if args.format == "json":
        print(json.dumps(stats, indent=2))
    else:
        print(render_markdown(stats))


if __name__ == "__main__":
    main()
