"""Tests for the authority graph."""

from __future__ import annotations

from repoctx.authority.graph import build_authority_graph
from repoctx.authority.records import AuthorityLevel, AuthorityRecord


def _record(**overrides) -> AuthorityRecord:
    defaults = dict(
        id="contract:contracts/tokens.md",
        type="contract",
        path="contracts/tokens.md",
        title="Tokens",
        summary="Token contract",
        text="",
        authority_level=AuthorityLevel.HARD,
        tags=["contract"],
        applies_to_paths=["app/auth/**"],
    )
    defaults.update(overrides)
    return AuthorityRecord(**defaults)


def test_implemented_by_edges():
    records = [_record()]
    graph = build_authority_graph(
        records,
        file_paths=["app/auth/tokens.py", "app/auth/session.py", "app/unrelated.py"],
        test_paths=[],
    )
    impl = graph.targets("implemented_by", "contract:contracts/tokens.md")
    assert impl == {"app/auth/tokens.py", "app/auth/session.py"}
    assert graph.sources("implemented_by", "app/auth/tokens.py") == {"contract:contracts/tokens.md"}


def test_enforced_by_edges():
    records = [_record(applies_to_paths=["tests/contracts/test_tokens.py"])]
    graph = build_authority_graph(
        records,
        file_paths=["tests/contracts/test_tokens.py"],
        test_paths=["tests/contracts/test_tokens.py"],
    )
    assert "tests/contracts/test_tokens.py" in graph.targets(
        "enforced_by", "contract:contracts/tokens.md"
    )


def test_no_edges_when_no_matches():
    graph = build_authority_graph([_record()], file_paths=["other/file.py"], test_paths=[])
    assert graph.targets("implemented_by", "contract:contracts/tokens.md") == set()
