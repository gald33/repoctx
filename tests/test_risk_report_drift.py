"""Integration test: risk_report detects drift via the authority graph."""

from __future__ import annotations

from pathlib import Path

import pytest

from repoctx.protocol import op_risk_report


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
        "---\n"
        "# Tokens contract\n## Invariants\n- tokens encrypted at rest\n"
    )
    src = tmp_path / "app" / "auth"
    src.mkdir(parents=True)
    (src / "tokens.py").write_text("def t(): return 1\n")
    return tmp_path


def test_drift_flagged_when_impl_changes_but_contract_does_not(repo: Path) -> None:
    out = op_risk_report("tokens", ["app/auth/tokens.py"], repo_root=repo)
    risks = out["risk_notes"]
    drift = [r for r in risks if "drift" in r["risk"].lower()]
    assert drift, f"expected drift risk, got {risks}"
    assert drift[0]["severity"] == "advisory"


def test_no_drift_when_contract_file_also_changed(repo: Path) -> None:
    out = op_risk_report(
        "tokens",
        ["app/auth/tokens.py", "contracts/tokens.md"],
        repo_root=repo,
    )
    drift = [r for r in out["risk_notes"] if "drift" in r["risk"].lower()]
    assert not drift


def test_constraint_violation_flagged_hard(repo: Path) -> None:
    out = op_risk_report("tokens", ["app/auth/tokens.py"], repo_root=repo)
    hard = [r for r in out["risk_notes"] if r["severity"] == "hard"]
    # Hard constraint governs app/auth/**; a change there must be surfaced hard.
    assert hard, f"expected hard constraint risk, got {out['risk_notes']}"
