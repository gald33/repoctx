"""Tests for Phase-3 constraint extraction."""

from __future__ import annotations

from pathlib import Path

import pytest

from repoctx.authority import AuthorityProducer
from repoctx.authority.extract import (
    extract_constraints,
    extract_heading_bullets,
    parse_front_matter,
)


def test_parse_front_matter_scalars_and_lists():
    text = (
        "---\n"
        "id: auth/session-tokens\n"
        "severity: hard\n"
        "applies_to:\n"
        "  - app/auth/**\n"
        "  - app/session/**\n"
        "validated_by:\n"
        "  - tests/contracts/test_session_tokens.py\n"
        "---\n"
        "# Title\nbody\n"
    )
    fm, rest = parse_front_matter(text)
    assert fm["id"] == "auth/session-tokens"
    assert fm["severity"] == "hard"
    assert fm["applies_to"] == ["app/auth/**", "app/session/**"]
    assert fm["validated_by"] == ["tests/contracts/test_session_tokens.py"]
    assert rest.startswith("# Title")


def test_parse_front_matter_absent():
    fm, rest = parse_front_matter("# Title\nbody\n")
    assert fm == {}
    assert rest.startswith("# Title")


def test_extract_heading_bullets_only_collects_constraint_sections():
    text = (
        "## Summary\n"
        "- ignored\n"
        "## Invariants\n"
        "- tokens must be encrypted\n"
        "- sessions expire within 24h\n"
        "## Notes\n"
        "- noise\n"
        "## Do not\n"
        "- mutate existing thought records\n"
    )
    sections = extract_heading_bullets(text)
    titles = [t for t, _ in sections]
    assert "invariants" in titles
    assert "do not" in titles
    assert "summary" not in titles
    bullets = {t: items for t, items in sections}
    assert len(bullets["invariants"]) == 2
    assert bullets["do not"] == ["mutate existing thought records"]


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "AGENTS.md").write_text("# Agents\n")
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (contracts / "tokens.md").write_text(
        "---\n"
        "id: auth/tokens\n"
        "severity: hard\n"
        "applies_to:\n"
        "  - app/auth/**\n"
        "validated_by:\n"
        "  - tests/contracts/test_tokens.py\n"
        "---\n"
        "# Tokens contract\n\n"
        "## Invariants\n"
        "- tokens must be encrypted at rest\n"
        "- tokens must rotate every 24h\n"
        "## Do not\n"
        "- log token values\n"
    )
    src = tmp_path / "app" / "auth"
    src.mkdir(parents=True)
    (src / "tokens.py").write_text("# INVARIANT: no plaintext tokens\ndef t(): return 1\n")
    return tmp_path


def test_extract_constraints_uses_front_matter_and_bullets(repo: Path) -> None:
    records = AuthorityProducer(repo).build_authority_records()
    constraints = extract_constraints(records)
    contract_constraints = [c for c in constraints if c.source_record_id.startswith("contract:")]
    assert len(contract_constraints) >= 3  # 2 invariants + 1 do-not bullet
    for c in contract_constraints:
        assert c.severity == "hard"
        assert c.applies_to_paths == ["app/auth/**"]
        assert "tests/contracts/test_tokens.py" in [r.split(":", 1)[1] for r in c.validation_refs]


def test_extract_constraints_includes_inline_markers(repo: Path) -> None:
    records = AuthorityProducer(repo).build_authority_records()
    constraints = extract_constraints(records)
    inline = [c for c in constraints if c.severity == "hard" and "app/auth/tokens.py" in c.applies_to_paths]
    assert inline, "inline INVARIANT: marker must produce a hard constraint scoped to its file"


def test_extract_constraints_deduplicates(repo: Path) -> None:
    records = AuthorityProducer(repo).build_authority_records()
    constraints = extract_constraints(records)
    ids = [c.id for c in constraints]
    assert len(ids) == len(set(ids))
