"""`repoctx reporting` — inspect and toggle anonymous usage reporting.

Subactions:
  status   Show the current effective state, channel, install_id, queue size.
  on       Enable reporting (writes enabled=true to ~/.repoctx/reporting.json).
  off      Disable reporting. Pass --purge to also drop the pending queue.
  show     Print the most recent queued events that *would* be uploaded.
  flush    Attempt to upload the queue now.

Stable builds default to OFF (no prompts, ever) — users opt in explicitly.
Canary builds default to ON with a one-time disclosure notice.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from repoctx import reporting

NAME = "reporting"


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        NAME,
        help="Inspect or toggle anonymous usage reporting (off by default on stable).",
        description=(
            "Manage the anonymous usage-reporting state for this install.\n\n"
            "Stable channel: reporting is OFF by default. You can opt in with\n"
            "`repoctx reporting on`. Canary channel: reporting is ON by default;\n"
            "disable with `repoctx reporting off` or REPOCTX_REPORTING=off."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "action",
        nargs="?",
        default="status",
        choices=["status", "on", "off", "show", "flush"],
        help="What to do (default: status).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="For `show`: max number of queued events to print (default 10).",
    )
    parser.add_argument(
        "--purge",
        action="store_true",
        help="For `off`: also drop the pending queue. Without this, queued events stay for if you re-enable later.",
    )
    parser.add_argument(
        "--format",
        choices=["json", "text"],
        default="text",
        help="Output format (default text).",
    )


def run(args: argparse.Namespace) -> None:
    action = args.action

    if action == "status":
        _print_status(args)
        return

    if action == "on":
        reporting.set_enabled(True)
        _print_status(args, prefix="Reporting enabled.")
        return

    if action == "off":
        reporting.set_enabled(False)
        dropped_bytes = 0
        if args.purge:
            dropped_bytes = reporting.purge_queue()
        prefix = "Reporting disabled."
        if args.purge:
            prefix += f" Purged {dropped_bytes} bytes of queued events."
        _print_status(args, prefix=prefix)
        return

    if action == "show":
        events = reporting.get_queued_events(limit=args.limit)
        if args.format == "json":
            print(json.dumps(events, indent=2, sort_keys=True))
        else:
            if not events:
                print("(no queued events)")
                return
            print(f"Showing {len(events)} most recent queued event(s):\n")
            for event in events:
                print(json.dumps(event, sort_keys=True))
        return

    if action == "flush":
        result = reporting.flush()
        payload = {
            "sent": result.sent,
            "accepted": result.accepted,
            "rejected": result.rejected,
            "error": result.error,
        }
        if args.format == "json":
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            if result.error:
                print(f"Flush failed: {result.error}")
            else:
                print(f"Flush sent {result.sent} event(s) "
                      f"(accepted={result.accepted}, rejected={result.rejected}).")
        return


def _print_status(args: argparse.Namespace, *, prefix: str | None = None) -> None:
    status = reporting.get_status()
    if args.format == "json":
        if prefix:
            status["_message"] = prefix
        print(json.dumps(status, indent=2, sort_keys=True))
        return

    if prefix:
        print(prefix)
    print(_render_text_status(status))


def _render_text_status(status: dict[str, Any]) -> str:
    lines = [
        f"channel:          {status['channel']}",
        f"build_id:         {status['build_id']}",
        f"install_id:       {status['install_id']}",
        f"enabled:          {status['enabled']}  (source: {status['enabled_source']})",
        f"channel default:  {status['channel_default']}",
        f"endpoint:         {status['endpoint'] or '(none — using local LoggingPoster)'}",
        f"queue size:       {status['queue_bytes']} bytes",
        f"queue path:       {status['queue_path']}",
    ]
    return "\n".join(lines)
