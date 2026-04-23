"""Tests for inline authority-marker parsing."""

from __future__ import annotations

from repoctx.authority.markers import parse_markers


def test_parses_common_comment_prefixes():
    text = "\n".join(
        [
            "# INVARIANT: thoughts are append-only",
            "// CONTRACT: session tokens must be encrypted",
            "-- DO NOT: mutate existing thought records",
            "; IMPORTANT: keep the hot path cache-warm",
            "<!-- See contract: contracts/auth/session-tokens.md -->",
            "# not a marker",
            "plain prose",
        ]
    )
    markers = parse_markers(text)
    kinds = [m.kind for m in markers]
    bodies = [m.body for m in markers]
    assert kinds == ["invariant", "contract", "do_not", "important", "see_contract"]
    assert "append-only" in bodies[0]
    assert bodies[4] == "contracts/auth/session-tokens.md"


def test_ignores_empty_bodies_and_non_markers():
    text = "# INVARIANT:\n# random comment\n# INVARIANT: real rule\n"
    markers = parse_markers(text)
    assert len(markers) == 1
    assert markers[0].body == "real rule"
    assert markers[0].line == 3


def test_case_insensitive_kinds():
    markers = parse_markers("# invariant: lower\n# Important: mixed\n")
    assert [m.kind for m in markers] == ["invariant", "important"]
