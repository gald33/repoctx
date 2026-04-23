"""Cursor harness adapter.

Cursor reads MCP server configs from ``.cursor/mcp.json`` inside the project.
Agent-facing instructions live in ``AGENTS.md`` (same file as other adapters;
we append the same ground-truth section, which is harness-agnostic copy).
"""

from __future__ import annotations

import json
from pathlib import Path

from repoctx.harness.claude_code import (
    AGENTS_SECTION_HEADER,
    InstallResult,
    MCP_SERVER_NAME,
    _ensure_agents_section,
)


def install_cursor(repo_root: str | Path = ".") -> InstallResult:
    root = Path(repo_root).resolve()
    agents_md, agents_changed = _ensure_agents_section(root)
    mcp_config, mcp_changed = _ensure_cursor_mcp(root)
    return InstallResult(
        agents_md=agents_md,
        agents_md_changed=agents_changed,
        mcp_config=mcp_config,
        mcp_config_changed=mcp_changed,
    )


def _ensure_cursor_mcp(root: Path) -> tuple[Path, bool]:
    cursor_dir = root / ".cursor"
    path = cursor_dir / "mcp.json"
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
    cursor_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return path, True


__all__ = ["install_cursor"]
