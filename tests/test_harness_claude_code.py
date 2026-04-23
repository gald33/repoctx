"""Tests for the Claude Code harness installer (idempotent AGENTS + .mcp.json)."""

from __future__ import annotations

import json
from pathlib import Path

from repoctx.harness import AGENTS_SECTION_HEADER, install_claude_code


def test_installer_creates_agents_md_and_mcp_config(tmp_path: Path) -> None:
    result = install_claude_code(tmp_path)
    assert result.agents_md_changed
    assert result.mcp_config_changed
    agents = (tmp_path / "AGENTS.md").read_text()
    assert AGENTS_SECTION_HEADER in agents
    assert "repoctx.bundle(task)" in agents
    mcp = json.loads((tmp_path / ".mcp.json").read_text())
    assert "repoctx" in mcp["mcpServers"]


def test_installer_is_idempotent(tmp_path: Path) -> None:
    install_claude_code(tmp_path)
    second = install_claude_code(tmp_path)
    assert not second.agents_md_changed
    assert not second.mcp_config_changed


def test_installer_appends_to_existing_agents_md(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# Agents\n\nAlready has content.\n")
    install_claude_code(tmp_path)
    text = (tmp_path / "AGENTS.md").read_text()
    assert "Already has content." in text
    assert AGENTS_SECTION_HEADER in text


def test_installer_preserves_other_mcp_servers(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"other": {"command": "foo"}}})
    )
    install_claude_code(tmp_path)
    mcp = json.loads((tmp_path / ".mcp.json").read_text())
    assert "other" in mcp["mcpServers"]
    assert "repoctx" in mcp["mcpServers"]
