"""Tests for the Ground-Truth Bundle assembler."""

from __future__ import annotations

from pathlib import Path

import pytest

from repoctx.bundle import BUNDLE_SCHEMA_VERSION, build_bundle


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "AGENTS.md").write_text("# Agents\nCall repoctx first.\n")
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (contracts / "tokens.md").write_text("# Token contract\n\nNever persist plaintext tokens.\n")
    src = tmp_path / "app"
    src.mkdir()
    (src / "tokens.py").write_text(
        "# INVARIANT: tokens must not be persisted\n"
        "def make_token():\n    return 'x'\n"
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_tokens.py").write_text("def test_tokens():\n    assert True\n")
    return tmp_path


def test_bundle_has_schema_and_recall_contract(repo: Path) -> None:
    bundle = build_bundle("work on tokens", repo_root=repo)
    data = bundle.to_dict()
    assert data["schema_version"] == BUNDLE_SCHEMA_VERSION
    assert data["when_to_recall_repoctx"], "recall rules must always be present"
    assert data["before_finalize_checklist"], "finalize checklist must always be present"
    assert data["uncertainty_rule"], "uncertainty rule must always be present"


def test_bundle_surfaces_authority_first(repo: Path) -> None:
    bundle = build_bundle("tokens", repo_root=repo)
    levels = [int(r.authority_level) for r in bundle.authoritative_records]
    assert levels == sorted(levels), "records must be ordered hard→guided→implementation"
    assert any(r.type == "contract" for r in bundle.authoritative_records)


def test_bundle_produces_constraints_for_hard_authority(repo: Path) -> None:
    bundle = build_bundle("tokens", repo_root=repo)
    hard = [c for c in bundle.constraints if c.severity == "hard"]
    assert hard, "hard authority must yield at least one hard constraint"
    for c in hard:
        assert c.statement
        assert c.source_record_id


def test_bundle_scope_has_allowed_and_protected(repo: Path) -> None:
    bundle = build_bundle("tokens", repo_root=repo)
    scope = bundle.edit_scope
    assert scope.rationale
    # Contracts dir should land in protected_paths.
    assert any("contracts" in p for p in scope.protected_paths)


def test_bundle_metrics_report_ranker_mode(repo: Path) -> None:
    bundle = build_bundle("tokens", repo_root=repo)
    assert bundle.metrics["ranker"] == "lexical"

    bundle_emb = build_bundle(
        "tokens", repo_root=repo, embedding_scores={"app/tokens.py": 0.9}
    )
    assert bundle_emb.metrics["ranker"] == "embeddings"


def test_serialization_bounds_excerpts(repo: Path) -> None:
    big = "x" * 5000
    (repo / "contracts" / "big.md").write_text(f"# Big\n\n{big}\n")
    bundle = build_bundle("tokens", repo_root=repo)
    data = bundle.to_dict()
    for rec in data["authority"]["records"]:
        assert len(rec["excerpt"]) <= 800
