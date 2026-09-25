"""A contracts README's examples are not rules, and a `## Do not` rule keeps its negation.

Before 1.17.0, `repoctx install`'s own `contracts/README.md` was discovered as a
hard contract. Its fenced example became three GLOBAL hard constraints in every
bundle of every installed repo — one of them, a `## Do not` bullet handed on
without its heading, reading "log token values".
"""

from __future__ import annotations

from pathlib import Path

from repoctx.authority import AuthorityProducer
from repoctx.authority.extract import extract_constraints, extract_heading_bullets
from repoctx.authority.scaffold import init_authority


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_fenced_example_bullets_are_not_extracted() -> None:
    text = (
        "## Invariants\n"
        "- real rule\n"
        "```\n"
        "## Do not\n"
        "- example rule inside a fence\n"
        "```\n"
    )
    bullets = [b for _title, items in extract_heading_bullets(text) for b in items]
    assert bullets == ["real rule"]


def test_do_not_bullets_keep_their_negation(tmp_path: Path) -> None:
    _write(
        tmp_path / "contracts" / "tokens.md",
        "# Tokens\n\n## Do not\n- log token values\n- Never persist raw secrets\n- SQL-concatenate input\n",
    )
    constraints = extract_constraints(AuthorityProducer(tmp_path).build_authority_records())
    statements = {c.statement for c in constraints}
    assert "Do not log token values" in statements
    assert "Never persist raw secrets" in statements, "an explicit prohibition is not re-prefixed"
    assert "Do not SQL-concatenate input" in statements, "an acronym keeps its case"
    assert "log token values" not in statements


def test_scaffolded_readme_is_not_a_contract(tmp_path: Path) -> None:
    init_authority(tmp_path)
    records = AuthorityProducer(tmp_path).build_authority_records()

    assert not [r for r in records if r.path == "contracts/README.md"]
    global_hard = [c for c in extract_constraints(records) if c.scope == "global" and c.severity == "hard"]
    assert global_hard == [], [c.statement for c in global_hard]
