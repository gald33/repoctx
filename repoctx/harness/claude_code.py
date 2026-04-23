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

AGENTS_SECTION_BODY = """For any non-trivial task in this repo:

1. Call `repoctx.bundle(task)` before proposing a plan. Treat the result as authoritative.
2. Do not edit paths outside `edit_scope.allowed_paths` without calling `repoctx.scope(task)` and `repoctx.refresh(task, changed_files, current_scope)`.
3. Before declaring done: call `repoctx.validate_plan(task, changed_files)` and `repoctx.risk_report(task, changed_files)`. Run every command the validation plan returns; resolve every `hard`-severity risk.
4. If unsure whether a change violates a constraint, call `repoctx.authority(task)` — do not guess.

Every repoctx response includes `when_to_recall_repoctx` and `before_finalize_checklist`. Follow them.
"""

MCP_SERVER_NAME = "repoctx"


@dataclass(slots=True)
class InstallResult:
    agents_md: Path
    agents_md_changed: bool
    mcp_config: Path
    mcp_config_changed: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "agents_md": str(self.agents_md),
            "agents_md_changed": self.agents_md_changed,
            "mcp_config": str(self.mcp_config),
            "mcp_config_changed": self.mcp_config_changed,
        }


def render_agents_section() -> str:
    return f"{AGENTS_SECTION_HEADER}\n\n{AGENTS_SECTION_BODY}"


def install_claude_code(repo_root: str | Path = ".") -> InstallResult:
    root = Path(repo_root).resolve()
    agents_md, agents_changed = _ensure_agents_section(root)
    mcp_config, mcp_changed = _ensure_mcp_registration(root)
    return InstallResult(
        agents_md=agents_md,
        agents_md_changed=agents_changed,
        mcp_config=mcp_config,
        mcp_config_changed=mcp_changed,
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


__all__ = [
    "AGENTS_SECTION_HEADER",
    "InstallResult",
    "install_claude_code",
    "render_agents_section",
]
