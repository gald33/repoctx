"""Claude Code hook handlers for task-entry and task-exit nudges.

These are invoked as the ``repoctx hook prompt-nudge`` and ``repoctx hook
stop-check`` subcommands, which are wired into ``.claude/settings.json`` by
``repoctx install``. Each reads a Claude Code hook JSON payload from stdin
and emits a short reminder that nudges the agent toward repoctx's task-entry
(``bundle``) and task-exit (``validate_plan`` / ``risk_report``) operations.

Design goals:

* Always exit 0 — never block the user's flow.
* Be cheap and silent for trivial prompts / turns; only speak when there's
  a concrete reason.
* Keep the core logic as plain Python functions returning
  ``HookOutput(stdout, stderr)`` so it can be unit-tested without subprocess
  plumbing; the CLI shells are thin wrappers around them.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

ENTRY_REMINDER = (
    "🧭 **repoctx**: before proposing a plan, call "
    '`mcp__repoctx__bundle("<one-line task>")`. Before declaring done, '
    "call `mcp__repoctx__validate_plan` + `mcp__repoctx__risk_report`."
)
EXIT_REMINDER = (
    "🧭 **repoctx**: this turn made edits but `validate_plan`/`risk_report` "
    "were not called. Run them before stopping."
)
SKIP_REASON_SUFFIX = " If you decide to skip this, briefly state the reason."

MIN_SUBSTANTIVE_LEN = 40
SUBSTANTIVE_KEYWORDS = re.compile(
    r"\b(implement|refactor|fix|add|build|rewrite|migrate|integrate|design)\b",
    re.IGNORECASE,
)

EDIT_TOOL_NAMES = frozenset({"Edit", "Write", "MultiEdit"})
VALIDATE_PLAN_TOOL = "mcp__repoctx__validate_plan"

# Cap how far back we scan the transcript when we cannot identify the
# start of the current turn. Each line is parsed as JSON, so the bound
# matters for cost on long-running sessions.
TRANSCRIPT_TAIL_LINES = 200


@dataclass(slots=True)
class HookOutput:
    stdout: str = ""
    stderr: str = ""


def _learn_enabled(env: dict[str, str] | None) -> bool:
    source = env if env is not None else os.environ
    return source.get("REPOCTX_LEARN") == "1"


def _is_substantive(prompt: str) -> bool:
    """Spec rule: long OR contains an action keyword."""
    if not prompt:
        return False
    if len(prompt) > MIN_SUBSTANTIVE_LEN:
        return True
    return bool(SUBSTANTIVE_KEYWORDS.search(prompt))


def handle_prompt_submit(
    payload: dict, *, env: dict[str, str] | None = None
) -> HookOutput:
    """UserPromptSubmit handler — emit the entry reminder for substantive prompts."""
    raw = payload.get("prompt") or payload.get("user_prompt") or ""
    prompt = raw.strip() if isinstance(raw, str) else ""
    if not _is_substantive(prompt):
        return HookOutput()
    text = ENTRY_REMINDER
    if _learn_enabled(env):
        text += SKIP_REASON_SUFFIX
    return HookOutput(stdout=text)


def handle_stop(
    payload: dict,
    *,
    env: dict[str, str] | None = None,
    transcript_reader=None,
) -> HookOutput:
    """Stop handler — remind the agent to run validate_plan after edits.

    ``transcript_reader`` is an optional callable that takes a Path and
    returns the transcript text. Defaults to reading the path with utf-8.
    Tests pass an in-memory reader; production reads from disk.
    """
    if payload.get("stop_hook_active"):
        # Re-entry from within a Stop nudge would loop. Honor the contract.
        return HookOutput()

    raw_path = payload.get("transcript_path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return HookOutput()

    reader = transcript_reader or _read_transcript
    try:
        text = reader(Path(raw_path))
    except (FileNotFoundError, PermissionError, OSError):
        return HookOutput()
    if not isinstance(text, str):
        return HookOutput()

    edits, validates = count_turn_tool_uses(text)
    if edits > 0 and validates == 0:
        msg = EXIT_REMINDER
        if _learn_enabled(env):
            msg += SKIP_REASON_SUFFIX
        return HookOutput(stderr=msg)
    return HookOutput()


def _read_transcript(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def count_turn_tool_uses(transcript_text: str) -> tuple[int, int]:
    """Return ``(edit_count, validate_plan_count)`` for the current turn.

    The "current turn" is everything after the last user-role message in the
    JSONL transcript. If we cannot find a user message (unfamiliar shape),
    we fall back to scanning the last ``TRANSCRIPT_TAIL_LINES`` lines.
    """
    lines = transcript_text.splitlines()
    last_user_idx = -1
    for idx, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if _is_user_message(obj):
            last_user_idx = idx

    if last_user_idx == -1:
        scan = lines[-TRANSCRIPT_TAIL_LINES:]
    else:
        scan = lines[last_user_idx + 1 :]

    edits = 0
    validates = 0
    for line in scan:
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        for name in _iter_tool_use_names(obj):
            if name in EDIT_TOOL_NAMES:
                edits += 1
            elif name == VALIDATE_PLAN_TOOL:
                validates += 1
    return edits, validates


def _is_user_message(obj: object) -> bool:
    if not isinstance(obj, dict):
        return False
    if obj.get("type") == "user":
        return True
    msg = obj.get("message")
    if isinstance(msg, dict) and msg.get("role") == "user":
        return True
    return False


def _iter_tool_use_names(obj: object) -> Iterator[str]:
    """Yield every tool name referenced by a JSONL transcript event."""
    if not isinstance(obj, dict):
        return
    for key in ("tool_name", "name"):
        val = obj.get(key)
        if isinstance(val, str) and val:
            yield val
    msg = obj.get("message")
    if isinstance(msg, dict):
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = block.get("name")
                    if isinstance(name, str) and name:
                        yield name


# -- CLI shells ---------------------------------------------------------------


def _read_stdin_json() -> dict:
    try:
        text = sys.stdin.read()
    except (KeyboardInterrupt, OSError):
        return {}
    if not text.strip():
        return {}
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def _emit(out: HookOutput) -> None:
    if out.stdout:
        sys.stdout.write(out.stdout)
        if not out.stdout.endswith("\n"):
            sys.stdout.write("\n")
    if out.stderr:
        sys.stderr.write(out.stderr)
        if not out.stderr.endswith("\n"):
            sys.stderr.write("\n")


def cli_prompt_nudge() -> int:
    _emit(handle_prompt_submit(_read_stdin_json()))
    return 0


def cli_stop_check() -> int:
    _emit(handle_stop(_read_stdin_json()))
    return 0


__all__ = [
    "ENTRY_REMINDER",
    "EXIT_REMINDER",
    "SKIP_REASON_SUFFIX",
    "HookOutput",
    "cli_prompt_nudge",
    "cli_stop_check",
    "count_turn_tool_uses",
    "handle_prompt_submit",
    "handle_stop",
]
