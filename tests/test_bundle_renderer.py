"""Tests for the bundle markdown renderer."""

from __future__ import annotations

from pathlib import Path

import pytest

from repoctx.bundle import build_bundle, render_bundle_markdown


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "AGENTS.md").write_text("# Agents\n")
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (contracts / "tokens.md").write_text(
        "---\napplies_to:\n  - app/auth/**\nseverity: hard\n---\n"
        "# Tokens\n## Invariants\n- tokens encrypted\n"
    )
    src = tmp_path / "app" / "auth"
    src.mkdir(parents=True)
    (src / "tokens.py").write_text("def t(): return 1\n")
    return tmp_path


def test_renders_sections_in_expected_order(repo: Path) -> None:
    bundle = build_bundle("tokens", repo_root=repo)
    md = render_bundle_markdown(bundle)
    sections = [
        "# Ground-truth bundle",
        "## Authority",
        "## Constraints",
        "## Edit scope",
        "## Relevant code",
        "## Validation plan",
        "## Risk notes",
        "## When to recall repoctx",
        "## Before you finalize",
    ]
    last = -1
    for title in sections:
        idx = md.find(title)
        assert idx > last, f"section {title!r} missing or out of order"
        last = idx


def test_renders_uncertainty_rule_at_end(repo: Path) -> None:
    bundle = build_bundle("tokens", repo_root=repo)
    md = render_bundle_markdown(bundle)
    assert md.rstrip().splitlines()[-1].startswith(">")


def test_renders_constraint_severity_tag(repo: Path) -> None:
    bundle = build_bundle("tokens", repo_root=repo)
    md = render_bundle_markdown(bundle)
    assert "[hard]" in md
