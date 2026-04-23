"""Tests for Cursor + Codex harness installers."""

from __future__ import annotations

import json
from pathlib import Path

from repoctx.harness import AGENTS_SECTION_HEADER, install_codex, install_cursor


def test_install_cursor_creates_cursor_mcp(tmp_path: Path) -> None:
    result = install_cursor(tmp_path)
    assert result.mcp_config_changed
    assert result.mcp_config.name == "mcp.json"
    assert result.mcp_config.parent.name == ".cursor"
    config = json.loads(result.mcp_config.read_text())
    assert "repoctx" in config["mcpServers"]
    agents = (tmp_path / "AGENTS.md").read_text()
    assert AGENTS_SECTION_HEADER in agents


def test_install_cursor_idempotent(tmp_path: Path) -> None:
    install_cursor(tmp_path)
    second = install_cursor(tmp_path)
    assert not second.agents_md_changed
    assert not second.mcp_config_changed


def test_install_codex_creates_codex_mcp(tmp_path: Path) -> None:
    result = install_codex(tmp_path)
    assert result.mcp_config_changed
    assert result.mcp_config.parent.name == ".codex"
    config = json.loads(result.mcp_config.read_text())
    assert "repoctx" in config["mcpServers"]


def test_install_codex_preserves_other_servers(tmp_path: Path) -> None:
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"other": {"command": "x"}}})
    )
    install_codex(tmp_path)
    config = json.loads((tmp_path / ".codex" / "mcp.json").read_text())
    assert set(config["mcpServers"].keys()) == {"other", "repoctx"}
