"""Tests for the six protocol operations."""

from __future__ import annotations

from pathlib import Path

import pytest

from repoctx.protocol import (
    op_authority,
    op_bundle,
    op_refresh,
    op_risk_report,
    op_scope,
    op_validate_plan,
)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "AGENTS.md").write_text("# Agents\n")
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (contracts / "tokens.md").write_text("# Token contract\nNever persist tokens.\n")
    src = tmp_path / "app"
    src.mkdir()
    (src / "tokens.py").write_text(
        "# INVARIANT: tokens must not be persisted\ndef t():\n    return 1\n"
    )
    (src / "unrelated.py").write_text("def u():\n    return 2\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_tokens.py").write_text("def test_tokens():\n    assert True\n")
    return tmp_path


def test_op_bundle_returns_full_schema(repo: Path) -> None:
    out = op_bundle("tokens", repo_root=repo)
    assert out["schema_version"] == "repoctx-bundle/1"
    assert "authority" in out and "edit_scope" in out
    assert out["when_to_recall_repoctx"]


def test_op_authority_returns_authority_only(repo: Path) -> None:
    out = op_authority("tokens", repo_root=repo)
    assert set(out.keys()) == {"schema_version", "task", "authority", "uncertainty_rule"}


def test_op_scope_returns_edit_scope(repo: Path) -> None:
    out = op_scope("tokens", repo_root=repo)
    assert "edit_scope" in out
    assert "allowed_paths" in out["edit_scope"]


def test_op_validate_plan_augments_for_changed_files(repo: Path) -> None:
    out = op_validate_plan("tokens", ["app/tokens.py"], repo_root=repo)
    plan = out["validation_plan"]
    assert plan["tests"] or plan["commands"]


def test_op_risk_report_flags_protected_path(repo: Path) -> None:
    out = op_risk_report("tokens", ["contracts/tokens.md"], repo_root=repo)
    hard = [r for r in out["risk_notes"] if r["severity"] == "hard"]
    assert hard, "touching a contracts/* file should be flagged hard"


def test_op_risk_report_no_risks_for_unrelated_edit(repo: Path) -> None:
    out = op_risk_report("tokens", ["app/unrelated.py"], repo_root=repo)
    hard = [r for r in out["risk_notes"] if r["severity"] == "hard"]
    assert not hard


def test_op_refresh_reports_scope_delta(repo: Path) -> None:
    current = {"allowed_paths": [], "related_paths": [], "protected_paths": []}
    out = op_refresh("tokens", ["app/tokens.py"], current, repo_root=repo)
    delta = out["scope_delta"]
    assert "added_allowed_paths" in delta
    # New scope should add at least something compared to an empty current scope.
    added = (
        delta["added_allowed_paths"]
        + delta["added_related_paths"]
        + delta["added_protected_paths"]
    )
    assert added
