"""Tests for Cursor + Codex harness installers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repoctx import embeddings as embeddings_mod
from repoctx.harness import AGENTS_SECTION_HEADER, install_all, install_codex, install_cursor


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


# -- install_all + embedding index --------------------------------------------


class _FakeIndex:
    def __init__(self, n: int = 3) -> None:
        self._n = n
        self.saved_to: Path | None = None

    def __len__(self) -> int:
        return self._n

    def save(self, path: Path) -> None:
        self.saved_to = Path(path)
        Path(path).mkdir(parents=True, exist_ok=True)


def test_install_all_skips_index_when_extras_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embeddings_mod, "HAS_EMBEDDINGS", False)
    result = install_all(tmp_path, scaffold_authority=False)
    assert result["errors"] == {}
    assert result["installed"]["embedding_index"]["status"] == "skipped"
    assert "extras not installed" in result["installed"]["embedding_index"]["reason"]


def test_install_all_builds_index_when_extras_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeIndex(n=7)
    monkeypatch.setattr(embeddings_mod, "HAS_EMBEDDINGS", True)
    monkeypatch.setattr(embeddings_mod, "build_index", lambda root: fake)
    result = install_all(tmp_path, scaffold_authority=False)
    assert result["errors"] == {}
    info = result["installed"]["embedding_index"]
    assert info["status"] == "built"
    assert info["files"] == 7
    assert fake.saved_to is not None
    assert fake.saved_to.parent.name == ".repoctx"


def test_install_all_no_index_flag_omits_section(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Even when extras are present, build_index=False fully skips.
    monkeypatch.setattr(embeddings_mod, "HAS_EMBEDDINGS", True)
    def _explode(_root):  # pragma: no cover - must not be called
        raise AssertionError("build_index should not be invoked")
    monkeypatch.setattr(embeddings_mod, "build_index", _explode)
    result = install_all(tmp_path, scaffold_authority=False, build_index=False)
    assert result["errors"] == {}
    assert "embedding_index" not in result["installed"]


def test_install_all_with_index_errors_when_extras_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embeddings_mod, "HAS_EMBEDDINGS", False)
    result = install_all(tmp_path, scaffold_authority=False, build_index=True)
    assert "embedding_index" in result["errors"]
    assert "sentence-transformers" in result["errors"]["embedding_index"]
    assert "embedding_index" not in result["installed"]
