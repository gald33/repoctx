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


def test_installer_writes_post_tool_hook(tmp_path: Path) -> None:
    result = install_claude_code(tmp_path)
    assert result.settings_changed
    assert result.settings_path is not None
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    matchers = settings["hooks"]["PostToolUse"]
    assert any(
        any(h.get("command", "").startswith("repoctx update") for h in (m.get("hooks") or []))
        for m in matchers
    )


def test_installer_hook_is_idempotent(tmp_path: Path) -> None:
    install_claude_code(tmp_path)
    second = install_claude_code(tmp_path)
    assert not second.settings_changed
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    matchers = settings["hooks"]["PostToolUse"]
    repoctx_hooks = [
        h
        for m in matchers
        for h in (m.get("hooks") or [])
        if h.get("command", "").startswith("repoctx update")
    ]
    assert len(repoctx_hooks) == 1


def test_installer_preserves_existing_settings(tmp_path: Path) -> None:
    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    (settings_dir / "settings.json").write_text(
        json.dumps({"hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo"}]}]}})
    )
    install_claude_code(tmp_path)
    settings = json.loads((settings_dir / "settings.json").read_text())
    matchers = settings["hooks"]["PostToolUse"]
    commands = [
        h.get("command")
        for m in matchers
        for h in (m.get("hooks") or [])
    ]
    assert "echo" in commands
    assert any(c and c.startswith("repoctx update") for c in commands)


def test_installer_agents_section_includes_upkeep(tmp_path: Path) -> None:
    install_claude_code(tmp_path)
    text = (tmp_path / "AGENTS.md").read_text()
    assert "Embedding upkeep" in text
    assert "repoctx update" in text
