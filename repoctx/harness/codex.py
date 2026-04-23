"""Codex CLI harness adapter.

Codex reads MCP servers from ``~/.codex/config.toml``; for repo-local opt-in
we instead drop a ``.codex/mcp.json`` file that can be sourced by wrapper
scripts, and append the same ``AGENTS.md`` ground-truth section.

This adapter is intentionally minimal — the Codex configuration surface
evolves; the AGENTS.md section is what actually guides the agent.
"""

from __future__ import annotations

import json
from pathlib import Path

from repoctx.harness.claude_code import (
    InstallResult,
    MCP_SERVER_NAME,
    _ensure_agents_section,
)


def install_codex(repo_root: str | Path = ".") -> InstallResult:
    root = Path(repo_root).resolve()
    agents_md, agents_changed = _ensure_agents_section(root)
    mcp_config, mcp_changed = _ensure_codex_mcp(root)
    return InstallResult(
        agents_md=agents_md,
        agents_md_changed=agents_changed,
        mcp_config=mcp_config,
        mcp_config_changed=mcp_changed,
    )


def _ensure_codex_mcp(root: Path) -> tuple[Path, bool]:
    codex_dir = root / ".codex"
    path = codex_dir / "mcp.json"
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
    codex_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return path, True


__all__ = ["install_codex"]
