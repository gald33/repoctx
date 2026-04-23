"""Tests for AuthorityProducer — convention paths and inline markers."""

from __future__ import annotations

from pathlib import Path

import pytest

from repoctx.authority import AuthorityLevel, AuthorityProducer
from repoctx.authority.records import authority_record_to_retrievable


@pytest.fixture()
def sample_repo(tmp_path: Path) -> Path:
    (tmp_path / "AGENTS.md").write_text("# Agents\nFollow the rules.\n")
    contract_dir = tmp_path / "contracts" / "auth"
    contract_dir.mkdir(parents=True)
    (contract_dir / "session-tokens.md").write_text(
        "# Session-token contract\n\nTokens must not be written unencrypted.\n"
    )
    arch = tmp_path / "docs" / "architecture"
    arch.mkdir(parents=True)
    (arch / "overview.md").write_text("# Overview\nThe system has three layers.\n")
    ex = tmp_path / "examples" / "auth"
    ex.mkdir(parents=True)
    (ex / "refresh.py").write_text("# Example refresh\nprint('hello')\n")
    src = tmp_path / "app" / "auth"
    src.mkdir(parents=True)
    (src / "session.py").write_text(
        "# INVARIANT: session tokens must not be written to disk\n"
        "# IMPORTANT: this path is hot\n"
        "def refresh():\n    return True\n"
    )
    return tmp_path


def test_discovery_finds_convention_paths(sample_repo: Path) -> None:
    producer = AuthorityProducer(sample_repo)
    records = producer.build_authority_records()
    by_type: dict[str, list] = {}
    for r in records:
        by_type.setdefault(r.type, []).append(r)

    assert any(r.path == "AGENTS.md" for r in by_type.get("agent_instruction", []))
    assert any(r.path == "contracts/auth/session-tokens.md" for r in by_type.get("contract", []))
    assert any(r.path == "docs/architecture/overview.md" for r in by_type.get("architecture_note", []))
    assert any(r.path.startswith("examples/") for r in by_type.get("example", []))


def test_discovery_assigns_authority_levels(sample_repo: Path) -> None:
    records = AuthorityProducer(sample_repo).build_authority_records()
    contracts = [r for r in records if r.type == "contract" and not r.path.startswith("app/")]
    assert contracts and all(r.authority_level == AuthorityLevel.HARD for r in contracts)

    agents = [r for r in records if r.type == "agent_instruction"]
    assert agents and all(r.authority_level == AuthorityLevel.GUIDED for r in agents)


def test_inline_markers_produce_records(sample_repo: Path) -> None:
    records = AuthorityProducer(sample_repo).build_authority_records()
    inline = [r for r in records if "inline" in r.tags]
    assert any(r.type == "invariant" and r.authority_level == AuthorityLevel.HARD for r in inline)
    assert any(r.type == "architecture_note" and r.authority_level == AuthorityLevel.GUIDED for r in inline)
    inv = next(r for r in inline if r.type == "invariant")
    assert inv.applies_to_paths == ["app/auth/session.py"]


def test_produces_retrievable_records(sample_repo: Path) -> None:
    producer = AuthorityProducer(sample_repo)
    retrievables = producer.build_records()
    assert retrievables
    assert all(r.namespace == "authority" for r in retrievables)
    types = {r.record_type for r in retrievables}
    assert {"contract", "agent_instruction"}.issubset(types)


def test_authority_to_retrievable_roundtrip_metadata(sample_repo: Path) -> None:
    records = AuthorityProducer(sample_repo).build_authority_records()
    first = next(r for r in records if r.type == "contract")
    generic = authority_record_to_retrievable(first)
    assert generic.metadata["authority_level"] == int(first.authority_level)
    assert generic.metadata["authority_type"] == first.type
