"""Tests for the init-authority scaffolder."""

from __future__ import annotations

from pathlib import Path

from repoctx.authority.scaffold import init_authority


def test_creates_expected_files(tmp_path: Path) -> None:
    result = init_authority(tmp_path)
    rel = sorted(p.relative_to(tmp_path).as_posix() for p in result.created)
    assert rel == [
        "contracts/README.md",
        "contracts/example.md",
        "docs/architecture/README.md",
        "docs/architecture/example.md",
        "examples/README.md",
    ]


def test_is_idempotent(tmp_path: Path) -> None:
    init_authority(tmp_path)
    second = init_authority(tmp_path)
    assert not second.created
    assert len(second.skipped) == 5


def test_does_not_overwrite_existing_files(tmp_path: Path) -> None:
    (tmp_path / "contracts").mkdir()
    existing = tmp_path / "contracts" / "README.md"
    existing.write_text("keep me")
    init_authority(tmp_path)
    assert existing.read_text() == "keep me"


def test_scaffolded_contract_is_discoverable_as_authority(tmp_path: Path) -> None:
    from repoctx.authority import AuthorityProducer

    init_authority(tmp_path)
    records = AuthorityProducer(tmp_path).build_authority_records()
    assert any(r.type == "contract" and r.path == "contracts/example.md" for r in records)
    assert any(r.type == "architecture_note" and r.path.startswith("docs/architecture/") for r in records)


def test_scaffolded_contract_yields_constraints(tmp_path: Path) -> None:
    from repoctx.authority import AuthorityProducer
    from repoctx.authority.extract import extract_constraints

    init_authority(tmp_path)
    records = AuthorityProducer(tmp_path).build_authority_records()
    constraints = extract_constraints(records)
    # The example contract has `## Invariants` and `## Do not` sections,
    # so at least two bullets must surface.
    contract_constraints = [c for c in constraints if c.source_record_id.startswith("contract:")]
    assert len(contract_constraints) >= 2
