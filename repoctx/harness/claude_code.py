"""Claude Code harness adapter.

Does two things, idempotently:

1. Append a ``## Ground truth (repoctx)`` section to ``AGENTS.md`` at the repo
   root. If the section already exists, it is left alone.
2. Register the repoctx MCP server in ``.mcp.json`` at the repo root. If the
   server entry already exists, it is left alone.

Deliberately minimal: no hooks, no permission file rewrites, no assumption
that we control the Claude Code harness beyond what the repo can influence.
"""

from __future__ import annotations

import json
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
HOOK_COMMAND = "repoctx update --from-claude-hook"


@dataclass(slots=True)
class InstallResult:
    agents_md: Path
    agents_md_changed: bool
    mcp_config: Path
    mcp_config_changed: bool
    settings_path: Path | None = None
    settings_changed: bool = False

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
        return out


def render_agents_section() -> str:
    return f"{AGENTS_SECTION_HEADER}\n\n{AGENTS_SECTION_BODY}\n{EMBEDDING_UPKEEP_BLURB}"


def install_claude_code(repo_root: str | Path = ".") -> InstallResult:
    root = Path(repo_root).resolve()
    agents_md, agents_changed = _ensure_agents_section(root)
    mcp_config, mcp_changed = _ensure_mcp_registration(root)
    settings_path, settings_changed = _ensure_post_tool_hook(root)
    return InstallResult(
        agents_md=agents_md,
        agents_md_changed=agents_changed,
        mcp_config=mcp_config,
        mcp_config_changed=mcp_changed,
        settings_path=settings_path,
        settings_changed=settings_changed,
    )


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
    if MCP_SERVER_NAME in servers:
        return path, False
    servers[MCP_SERVER_NAME] = {
        "command": "python",
        "args": ["-m", "repoctx.mcp_server", "--repo", str(root)],
    }
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return path, True


def _ensure_post_tool_hook(root: Path) -> tuple[Path, bool]:
    """Register a PostToolUse hook that queues edits via `repoctx update`.

    Writes to ``.claude/settings.json`` so the hook is committed alongside the
    project. Idempotent: a matching hook entry is detected and left alone.
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
    post_tool = hooks.setdefault("PostToolUse", [])

    for entry in post_tool:
        if not isinstance(entry, dict):
            continue
        if entry.get("matcher") != HOOK_MATCHER:
            continue
        for h in entry.get("hooks", []) or []:
            if isinstance(h, dict) and h.get("command", "").startswith("repoctx update"):
                return path, False

    post_tool.append(
        {
            "matcher": HOOK_MATCHER,
            "hooks": [{"type": "command", "command": HOOK_COMMAND}],
        }
    )
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return path, True


__all__ = [
    "AGENTS_SECTION_HEADER",
    "EMBEDDING_UPKEEP_BLURB",
    "HOOK_COMMAND",
    "HOOK_MATCHER",
    "InstallResult",
    "install_claude_code",
    "render_agents_section",
]
