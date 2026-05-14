"""``repoctx hook`` subcommand — Claude Code hook handlers.

Each sub-action reads a Claude Code hook JSON payload from stdin and emits a
short reminder. Always exits 0 so it cannot block the user's flow. Wired
into ``.claude/settings.json`` by ``repoctx install`` /
``repoctx install-claude-code``.
"""

from __future__ import annotations

import argparse
from types import SimpleNamespace


def _register(subparsers) -> None:
    hk = subparsers.add_parser(
        "hook",
        help="Claude Code hook handlers (read JSON from stdin; exit 0 always)",
        description=(
            "Hook handlers wired into .claude/settings.json by `repoctx install`.\n"
            "Read Claude Code hook JSON from stdin; emit short reminders. Always\n"
            "exit 0 so they cannot block the user's flow."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = hk.add_subparsers(dest="hook_command", metavar="HOOK")
    sub.add_parser(
        "prompt-nudge",
        help=(
            "UserPromptSubmit: nudge the agent to call repoctx.bundle for "
            "substantive prompts"
        ),
    )
    sub.add_parser(
        "stop-check",
        help=(
            "Stop: nudge if the turn made edits without calling "
            "repoctx.validate_plan"
        ),
    )
    sub.add_parser(
        "tool-use",
        help=(
            "PostToolUse: append a tool_use feedback event for "
            "Read/Edit/Write/MultiEdit so the Phase 3 tuner can fit per-kind "
            "retrieval thresholds"
        ),
    )


def _run(args: argparse.Namespace) -> None:
    from repoctx.hooks import cli_prompt_nudge, cli_stop_check, cli_tool_use

    hook_command = getattr(args, "hook_command", None)
    if hook_command == "prompt-nudge":
        raise SystemExit(cli_prompt_nudge())
    if hook_command == "stop-check":
        raise SystemExit(cli_stop_check())
    if hook_command == "tool-use":
        raise SystemExit(cli_tool_use())
    # No sub-action given — print top-level help and exit 2 (usage error).
    raise SystemExit(2)


hook_cmd = SimpleNamespace(NAME="hook", register=_register, run=_run)
