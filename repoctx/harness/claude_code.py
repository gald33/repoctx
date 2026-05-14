"""Claude Code harness adapter.

Does three things, idempotently:

1. Append a ``## Ground truth (repoctx)`` section to ``AGENTS.md`` at the repo
   root. If the section already exists, it is left alone.
2. Register the repoctx MCP server in ``.mcp.json`` at the repo root. If the
   server entry already exists, it is left alone.
3. Place a short anchored "repoctx-nudge" block where Claude Code (and any
   other tool reading ``AGENTS.md``) will see it. Claude Code only auto-loads
   ``CLAUDE.md``; ``AGENTS.md`` is invisible to it unless something points
   there. Concretely:

   - ``CLAUDE.md`` absent → create it as a thin pointer (``@AGENTS.md`` import)
     and put the nudge in ``AGENTS.md``.
   - ``CLAUDE.md`` is a pointer (marker present, or short file with only an
     ``@AGENTS.md`` import + at most a title line) → nudge in ``AGENTS.md``
     only; ``CLAUDE.md`` stays a pointer.
   - Both files have substantive content → nudge in both, so single-file
     readers don't miss it.
   - ``CLAUDE.md`` has content + ``AGENTS.md`` is pointer/absent → nudge in
     ``CLAUDE.md`` only.

Deliberately minimal: no hooks beyond the existing PostToolUse one, no
permission file rewrites, no assumption that we control the Claude Code
harness beyond what the repo can influence.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

AGENTS_SECTION_HEADER = "## Ground truth (repoctx)"

EMBEDDING_UPKEEP_BLURB = """### Embedding upkeep

After every Edit / Write / MultiEdit on a tracked source file, run:

    repoctx update <relative-path>

The command queues the path and auto-flushes (debounced — defaults to every 10 edits or 5 minutes). It is cheap to call on every edit; if you forget, the next `repoctx.bundle` / `repoctx.scope` call also flushes pending updates before reading the index, so stale vectors are bounded by your read cadence.

For bulk catch-up (after a rebase, branch switch, or external edits) prefer `repoctx index --incremental` — it re-embeds only chunks whose `content_hash` changed, much cheaper than a full rebuild. Use `repoctx rebuild` if the index is missing or you suspect corruption. `repoctx update --status` shows the queue; `repoctx update --flush` forces an immediate flush.
"""

AGENTS_SECTION_BODY = """For any non-trivial task in this repo:

1. Call `repoctx.bundle(task)` before proposing a plan. Treat the result as authoritative.
2. Do not edit paths outside `edit_scope.allowed_paths` without calling `repoctx.scope(task)` and `repoctx.refresh(task, changed_files, current_scope)`.
3. Before declaring done: call `repoctx.validate_plan(task, changed_files)` and `repoctx.risk_report(task, changed_files)`. Run every command the validation plan returns; resolve every `hard`-severity risk.
4. If unsure whether a change violates a constraint, call `repoctx.authority(task)` — do not guess.

Every repoctx response includes `when_to_recall_repoctx` and `before_finalize_checklist`. Follow them.

### First-time setup (one-shot)

If `contracts/` and `docs/architecture/` only contain the scaffold (`README.md` + `example.md`), repoctx has no real ground truth to surface. Bootstrap it once:

1. Call `repoctx.propose_authority()`. It returns `agent_brief` (markdown instructions), `suggested_files` (concrete paths to write), and detected `subsystems` + `contract_candidates`.
2. Author each file in `suggested_files` using the `agent_brief` conventions. Read 2–3 sample files per subsystem first — describe what *is* true, not what *should* be true.
3. Re-run `repoctx.bundle("sanity check")` to confirm the new authority loads.
"""

MCP_SERVER_NAME = "repoctx"

HOOK_MATCHER = "Edit|Write|MultiEdit"
# Suffix form: what the user sees in settings.json after ``<python> -m ``. We
# keep these as the legible "what this hook does" strings; the install code
# prepends ``sys.executable -m`` at write time so the hook fires regardless
# of whether the user's shell PATH includes the venv's ``repoctx`` binary.
HOOK_COMMAND = "repoctx update --from-claude-hook"

# Task-entry / task-exit nudge hooks. Wired in alongside the PostToolUse
# embedding-update hook so the agent gets harness-level prompts to call
# `mcp__repoctx__bundle` on substantive prompts and to call
# `mcp__repoctx__validate_plan` before stopping.
PROMPT_NUDGE_COMMAND = "repoctx hook prompt-nudge"
STOP_CHECK_COMMAND = "repoctx hook stop-check"

# Feedback-loop tool-use hook. Separate from the embedding-update hook because
# the matcher includes Read (you don't want to re-embed on every read) and the
# command is silent (no agent-facing output). Writes to the per-repo
# feedback-events.jsonl which the Phase 3 tuner consumes.
TOOL_USE_HOOK_MATCHER = "Read|Edit|Write|MultiEdit"
TOOL_USE_HOOK_COMMAND = "repoctx hook tool-use"


def _resolve_repoctx_invocation(suffix: str) -> str:
    """Build a shell command that runs ``repoctx <suffix>`` via the interpreter
    that ran ``repoctx install``.

    Why: hooks and the MCP server are launched by Claude Code (or another
    host) via the user's shell, whose PATH may not include the venv where
    ``repoctx`` was installed. Pinning to ``sys.executable`` makes the
    install self-contained regardless of how the user installed (venv,
    pipx, uv tool, system pip).

    ``suffix`` must start with ``repoctx ``; we strip the leading word and
    invoke ``-m repoctx`` so the same constants stay legible to anyone
    reading ``.claude/settings.json``.
    """
    if not suffix.startswith("repoctx "):
        raise ValueError(f"repoctx invocation must start with 'repoctx ': {suffix!r}")
    args = suffix[len("repoctx "):]
    return f"{shlex.quote(sys.executable)} -m repoctx {args}"

CLAUDE_MD_FILENAME = "CLAUDE.md"
AGENTS_MD_FILENAME = "AGENTS.md"

NUDGE_MARKER_V1 = "<!-- repoctx-nudge:v1 -->"
NUDGE_MARKER_V2 = "<!-- repoctx-nudge:v2 -->"
# ``NUDGE_MARKER`` always points at the current scaffold version. Existing
# code that imports it stays correct; tests that need to detect the older
# generation specifically should import ``NUDGE_MARKER_V1``.
NUDGE_MARKER = NUDGE_MARKER_V2

NUDGE_BLOCK = """\
<!-- repoctx-nudge:v2 -->
> **repoctx is installed for this repo.** For any non-trivial task you
> **must call** `mcp__repoctx__bundle(task)` before proposing a plan, and
> `mcp__repoctx__validate_plan` + `mcp__repoctx__risk_report` before
> declaring done. Use `mcp__repoctx__authority(task)` if unsure whether
> a change violates a constraint.
>
> **Non-trivial = touches >1 file OR introduces new behavior OR
> adds/removes a public API.** Single-file typo/rename/comment-only
> changes are trivial.
"""

POINTER_MARKER = "<!-- repoctx-pointer:v1 -->"
POINTER_TEMPLATE = """\
<!-- repoctx-pointer:v1 -->
<!-- This CLAUDE.md is a thin pointer managed by repoctx so Claude Code -->
<!-- transitively loads AGENTS.md. Edit AGENTS.md for project guidance. -->
@AGENTS.md
"""

# Backward-compat aliases (kept for one release; new code should use the names
# above without the CLAUDE_MD_ prefix since the block now lives in either file).
CLAUDE_MD_NUDGE_MARKER = NUDGE_MARKER
CLAUDE_MD_NUDGE_BLOCK = NUDGE_BLOCK

ENV_DISABLE_CLAUDE_MD_NUDGE = "REPOCTX_NO_CLAUDE_MD_NUDGE"

# Files whose content is mostly an import directive plus at most this many
# substantive lines (non-blank, non-import, non-comment) are treated as
# pointers. One line allows for a single title like ``# Project``.
_POINTER_MAX_BYTES = 500
_POINTER_MAX_SUBSTANTIVE_LINES = 1


# Action enum (string values so they serialize cleanly into JSON).
ACTION_SKIPPED = "skipped"
ACTION_NO_OP = "no_op"
ACTION_NUDGE_INSERTED = "nudge_inserted"
ACTION_POINTER_CREATED = "pointer_created"


@dataclass(slots=True)
class NudgeResult:
    """Outcome of placing the repoctx-nudge block across CLAUDE.md / AGENTS.md.

    ``*_action`` is one of ``skipped`` (feature disabled or file absent and
    not actionable), ``no_op`` (file already had the block), ``nudge_inserted``
    (block was added on this run), or ``pointer_created`` (CLAUDE.md only —
    the file was created as a thin ``@AGENTS.md`` pointer).
    """

    claude_md: Path
    claude_md_action: str
    agents_md: Path
    agents_md_action: str

    @property
    def claude_md_changed(self) -> bool:
        return self.claude_md_action in {ACTION_NUDGE_INSERTED, ACTION_POINTER_CREATED}

    @property
    def agents_md_changed(self) -> bool:
        return self.agents_md_action == ACTION_NUDGE_INSERTED

    def to_dict(self) -> dict[str, object]:
        return {
            "claude_md": str(self.claude_md),
            "claude_md_action": self.claude_md_action,
            "agents_md": str(self.agents_md),
            "agents_md_action": self.agents_md_action,
            "any_inserted": self.claude_md_changed or self.agents_md_changed,
        }


@dataclass(slots=True)
class InstallResult:
    agents_md: Path
    agents_md_changed: bool
    mcp_config: Path
    mcp_config_changed: bool
    settings_path: Path | None = None
    settings_changed: bool = False
    # Nudge fields. ``claude_md_changed`` is True if either the pointer was
    # created or the nudge block was inserted; ``claude_md_action`` reports
    # which (and "skipped"/"no_op" for the inactive cases).
    claude_md: Path | None = None
    claude_md_changed: bool = False
    claude_md_action: str | None = None
    agents_md_nudge_changed: bool = False

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "agents_md": str(self.agents_md),
            "agents_md_changed": self.agents_md_changed,
            "mcp_config": str(self.mcp_config),
            "mcp_config_changed": self.mcp_config_changed,
        }
        if self.settings_path is not None:
            out["settings_path"] = str(self.settings_path)
            out["settings_changed"] = self.settings_changed
        if self.claude_md is not None:
            out["claude_md"] = str(self.claude_md)
            out["claude_md_changed"] = self.claude_md_changed
            if self.claude_md_action is not None:
                out["claude_md_action"] = self.claude_md_action
        if self.agents_md_nudge_changed:
            out["agents_md_nudge_changed"] = True
        return out


def render_agents_section() -> str:
    return f"{AGENTS_SECTION_HEADER}\n\n{AGENTS_SECTION_BODY}\n{EMBEDDING_UPKEEP_BLURB}"


def install_claude_code(
    repo_root: str | Path = ".",
    *,
    claude_md_nudge: bool = True,
) -> InstallResult:
    root = Path(repo_root).resolve()
    agents_md, agents_changed = _ensure_agents_section(root)
    mcp_config, mcp_changed = _ensure_mcp_registration(root)
    settings_path, settings_changed = _ensure_post_tool_hook(root)
    nudge: NudgeResult | None = None
    if claude_md_nudge:
        nudge = ensure_claude_md_nudge(root)
    return InstallResult(
        agents_md=agents_md,
        agents_md_changed=agents_changed,
        mcp_config=mcp_config,
        mcp_config_changed=mcp_changed,
        settings_path=settings_path,
        settings_changed=settings_changed,
        claude_md=nudge.claude_md if nudge is not None else None,
        claude_md_changed=nudge.claude_md_changed if nudge is not None else False,
        claude_md_action=nudge.claude_md_action if nudge is not None else None,
        agents_md_nudge_changed=nudge.agents_md_changed if nudge is not None else False,
    )


def ensure_claude_md_nudge(
    repo_root: str | Path = ".",
    *,
    enabled: bool = True,
) -> NudgeResult:
    """Place the anchored repoctx-nudge block per the file layout.

    Claude Code auto-loads ``CLAUDE.md`` but not ``AGENTS.md``. To make the
    nudge visible to whichever tool is reading, we classify each file as
    ``absent``, ``pointer`` (a short file whose only content is an
    ``@OTHER.md`` import, or one we created with the pointer marker), or
    ``content``, then place the block as follows:

    ===========================  ==============================================
    State                        Action
    ===========================  ==============================================
    CLAUDE absent + AGENTS hassic content   create CLAUDE pointer; nudge in AGENTS
    CLAUDE pointer + AGENTS content   nudge in AGENTS only
    CLAUDE content + AGENTS content   nudge in BOTH
    CLAUDE content + AGENTS pointer/absent   nudge in CLAUDE only
    Otherwise (e.g. both absent) skip both
    ===========================  ==============================================

    Idempotent: if a file already has the marker, it's left byte-identical.
    Disabled when ``enabled=False`` or when ``REPOCTX_NO_CLAUDE_MD_NUDGE``
    is truthy in the environment.
    """
    root = Path(repo_root).resolve()
    claude_md = root / CLAUDE_MD_FILENAME
    agents_md = root / AGENTS_MD_FILENAME

    if not enabled or _nudge_disabled_in_env():
        return NudgeResult(
            claude_md=claude_md,
            claude_md_action=ACTION_SKIPPED,
            agents_md=agents_md,
            agents_md_action=ACTION_SKIPPED,
        )

    claude_state = _classify_md(claude_md, AGENTS_MD_FILENAME)
    agents_state = _classify_md(agents_md, CLAUDE_MD_FILENAME)

    claude_action = ACTION_SKIPPED if claude_state == "absent" else ACTION_NO_OP
    agents_action = ACTION_SKIPPED if agents_state == "absent" else ACTION_NO_OP

    # Create CLAUDE.md as a pointer if absent and AGENTS.md has real content
    # to point at. We won't create CLAUDE.md if AGENTS.md is also missing or
    # itself a pointer — the result would be a broken chain.
    if claude_state == "absent" and agents_state == "content":
        _create_pointer_claude_md(claude_md)
        claude_action = ACTION_POINTER_CREATED
        claude_state = "pointer"

    # Insert the nudge wherever there's substantive content.
    if claude_state == "content":
        if _insert_nudge_into_file(claude_md):
            claude_action = ACTION_NUDGE_INSERTED
    if agents_state == "content":
        if _insert_nudge_into_file(agents_md):
            agents_action = ACTION_NUDGE_INSERTED

    return NudgeResult(
        claude_md=claude_md,
        claude_md_action=claude_action,
        agents_md=agents_md,
        agents_md_action=agents_action,
    )


def _classify_md(path: Path, other_filename: str) -> str:
    """Return ``"absent"`` | ``"pointer"`` | ``"content"`` for a markdown file.

    A file is a *pointer* if it carries the repoctx pointer marker (we created
    it) **or** is short enough to be a hand-written one-liner (≤500 bytes
    total, contains an ``@OTHER.md`` import line, and has at most one
    substantive non-import non-comment line — typically a title).
    """
    if not path.exists():
        return "absent"
    text = path.read_text(encoding="utf-8")
    if POINTER_MARKER in text:
        return "pointer"
    if len(text) > _POINTER_MAX_BYTES:
        return "content"
    has_import = any(
        line.strip() == f"@{other_filename}" for line in text.splitlines()
    )
    if not has_import:
        return "content"
    substantive = [
        line for line in text.splitlines()
        if line.strip()
        and not line.strip().startswith("@")
        and not line.strip().startswith("<!--")
    ]
    if len(substantive) <= _POINTER_MAX_SUBSTANTIVE_LINES:
        return "pointer"
    return "content"


def _create_pointer_claude_md(path: Path) -> None:
    """Write the canonical CLAUDE.md pointer template."""
    path.write_text(POINTER_TEMPLATE, encoding="utf-8")


def _insert_nudge_into_file(path: Path) -> bool:
    """Insert or upgrade the nudge block in ``path``. Idempotent.

    - v2 marker present → no-op.
    - v1 marker present → rewrite the v1 block in place with the v2 block,
      preserving everything before/after it.
    - Neither marker → insert v2 block via :func:`_render_with_nudge_inserted`.
    """
    text = path.read_text(encoding="utf-8")
    if NUDGE_MARKER_V2 in text:
        return False
    if NUDGE_MARKER_V1 in text:
        upgraded = _upgrade_v1_nudge_block(text)
        if upgraded == text:
            return False
        path.write_text(upgraded, encoding="utf-8")
        return True
    path.write_text(_render_with_nudge_inserted(text), encoding="utf-8")
    return True


def _upgrade_v1_nudge_block(text: str) -> str:
    """Replace the v1 anchored block with the v2 block, preserving surroundings.

    The v1 block we wrote is a single ``<!-- repoctx-nudge:v1 -->`` marker
    line followed by a contiguous run of blockquote lines starting with
    ``>``. The block ends at the first non-blockquote, non-blank line (or
    EOF). We match that range and substitute the v2 block in its place.
    """
    lines = text.splitlines(keepends=True)
    start = None
    for i, line in enumerate(lines):
        if line.rstrip("\n") == NUDGE_MARKER_V1:
            start = i
            break
    if start is None:
        return text

    end = start + 1
    while end < len(lines):
        stripped = lines[end].lstrip()
        if stripped.startswith(">"):
            end += 1
            continue
        if lines[end].strip() == "":
            # A blank line inside the block terminates the block. We stop
            # before it so the blank line stays as the separator.
            break
        break

    new_block = NUDGE_BLOCK if NUDGE_BLOCK.endswith("\n") else NUDGE_BLOCK + "\n"
    return "".join(lines[:start]) + new_block + "".join(lines[end:])


def _nudge_disabled_in_env() -> bool:
    val = os.environ.get(ENV_DISABLE_CLAUDE_MD_NUDGE, "")
    return val.strip().lower() in ("1", "true", "yes", "on")


# Backward-compat shim for older imports.
_claude_md_nudge_disabled_in_env = _nudge_disabled_in_env


def _render_with_nudge_inserted(text: str) -> str:
    """Insert the nudge block before the first ``---`` line, else at EOF.

    Mirrors the bash reference implementation: insert the block immediately
    before the first horizontal-rule separator if one exists (with a
    blank-line separator after it), otherwise append it at the end with a
    blank line of separation.
    """
    block = NUDGE_BLOCK
    if not block.endswith("\n"):
        block += "\n"
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.rstrip("\n") == "---":
            return "".join(lines[:i]) + block + "\n" + "".join(lines[i:])
    suffix = "" if text.endswith("\n") else "\n"
    suffix += "\n"  # blank-line separator before the appended block
    return text + suffix + block


# Backward-compat alias for the old private name.
_insert_claude_md_nudge = _render_with_nudge_inserted


def _ensure_agents_section(root: Path) -> tuple[Path, bool]:
    path = root / "AGENTS.md"
    section = render_agents_section()
    if not path.exists():
        path.write_text(f"# Agents\n\n{section}\n", encoding="utf-8")
        return path, True
    existing = path.read_text(encoding="utf-8")
    if AGENTS_SECTION_HEADER in existing:
        return path, False
    separator = "\n" if existing.endswith("\n") else "\n\n"
    path.write_text(existing + separator + section + "\n", encoding="utf-8")
    return path, True


def _ensure_mcp_registration(root: Path) -> tuple[Path, bool]:
    path = root / ".mcp.json"
    if path.exists():
        try:
            config = json.loads(path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError:
            config = {}
    else:
        config = {}
    servers = config.setdefault("mcpServers", {})
    # Pin to the interpreter that ran ``repoctx install`` — the host launches
    # the MCP server via the shell, whose PATH may not include the venv.
    desired = {
        "command": sys.executable,
        "args": ["-m", "repoctx.mcp_server", "--repo", str(root)],
    }
    existing = servers.get(MCP_SERVER_NAME)
    if existing == desired:
        return path, False
    # Upgrade in place: older installs wrote ``"command": "python"`` which
    # silently fails when the host's shell PATH doesn't include the venv.
    servers[MCP_SERVER_NAME] = desired
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return path, True


def _ensure_post_tool_hook(root: Path) -> tuple[Path, bool]:
    """Register the three Claude Code hooks repoctx ships with.

    Writes to ``.claude/settings.json`` so hooks travel with the repo:

    - ``PostToolUse`` ``Edit|Write|MultiEdit`` → ``repoctx update --from-claude-hook``
      (keeps the embedding index live)
    - ``UserPromptSubmit`` → ``repoctx hook prompt-nudge``
      (task-entry nudge: ``bundle`` reminder for substantive prompts)
    - ``Stop`` → ``repoctx hook stop-check``
      (task-exit nudge: ``validate_plan`` reminder if edits happened)

    Idempotent. Each entry is detected by command prefix and skipped on
    re-install. Unrelated user-authored hooks under the same event are
    preserved. The function name and signature are kept stable for callers
    that already depend on them.
    """
    settings_dir = root / ".claude"
    settings_dir.mkdir(parents=True, exist_ok=True)
    path = settings_dir / "settings.json"

    if path.exists():
        try:
            settings = json.loads(path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError:
            settings = {}
    else:
        settings = {}
    if not isinstance(settings, dict):
        settings = {}

    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        hooks = {}
        settings["hooks"] = hooks

    changed = False
    changed |= _ensure_hook_entry(
        hooks,
        event="PostToolUse",
        command=_resolve_repoctx_invocation(HOOK_COMMAND),
        matcher=HOOK_MATCHER,
        command_marker="repoctx update",
    )
    changed |= _ensure_hook_entry(
        hooks,
        event="PostToolUse",
        command=_resolve_repoctx_invocation(TOOL_USE_HOOK_COMMAND),
        matcher=TOOL_USE_HOOK_MATCHER,
        command_marker=TOOL_USE_HOOK_COMMAND,
    )
    changed |= _ensure_hook_entry(
        hooks,
        event="UserPromptSubmit",
        command=_resolve_repoctx_invocation(PROMPT_NUDGE_COMMAND),
        matcher=None,
        command_marker=PROMPT_NUDGE_COMMAND,
    )
    changed |= _ensure_hook_entry(
        hooks,
        event="Stop",
        command=_resolve_repoctx_invocation(STOP_CHECK_COMMAND),
        matcher=None,
        command_marker=STOP_CHECK_COMMAND,
    )

    if changed:
        path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return path, changed


def _ensure_hook_entry(
    hooks: dict,
    *,
    event: str,
    command: str,
    matcher: str | None,
    command_marker: str,
) -> bool:
    """Append (or upgrade) a hook entry under ``hooks[event]``.

    Detection: any entry whose ``command`` contains ``command_marker`` is
    treated as our hook. If the stored command already equals what we'd
    write, the entry is left alone (idempotent re-install). If it differs
    — e.g., an older install wrote bare ``repoctx ...`` and the current
    binary writes ``<absolute python> -m repoctx ...`` — it's *rewritten*
    in place so existing installs auto-upgrade. Returns True iff anything
    was added or changed.

    When ``matcher`` is given, the search is narrowed to entries with that
    matcher; when ``None``, any entry under the event counts. Substring
    match (not prefix) because absolute interpreter paths now precede the
    legible marker.
    """
    entries = hooks.setdefault(event, [])
    if not isinstance(entries, list):
        entries = []
        hooks[event] = entries

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if matcher is not None and entry.get("matcher") != matcher:
            continue
        for h in entry.get("hooks", []) or []:
            if not isinstance(h, dict):
                continue
            cmd = h.get("command")
            if isinstance(cmd, str) and command_marker in cmd:
                if cmd == command:
                    return False  # already up-to-date
                h["command"] = command  # upgrade stale entry in place
                return True

    new_entry: dict[str, object] = {
        "hooks": [{"type": "command", "command": command}],
    }
    if matcher is not None:
        new_entry["matcher"] = matcher
    entries.append(new_entry)
    return True


__all__ = [
    "ACTION_NO_OP",
    "ACTION_NUDGE_INSERTED",
    "ACTION_POINTER_CREATED",
    "ACTION_SKIPPED",
    "AGENTS_MD_FILENAME",
    "AGENTS_SECTION_HEADER",
    "CLAUDE_MD_FILENAME",
    "CLAUDE_MD_NUDGE_BLOCK",
    "CLAUDE_MD_NUDGE_MARKER",
    "EMBEDDING_UPKEEP_BLURB",
    "ENV_DISABLE_CLAUDE_MD_NUDGE",
    "HOOK_COMMAND",
    "HOOK_MATCHER",
    "InstallResult",
    "NUDGE_BLOCK",
    "NUDGE_MARKER",
    "NUDGE_MARKER_V1",
    "NUDGE_MARKER_V2",
    "NudgeResult",
    "POINTER_MARKER",
    "PROMPT_NUDGE_COMMAND",
    "STOP_CHECK_COMMAND",
    "POINTER_TEMPLATE",
    "ensure_claude_md_nudge",
    "install_claude_code",
    "render_agents_section",
]
