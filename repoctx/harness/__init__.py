"""Harness-specific adapters for repoctx v2.

The core is environment-agnostic; these modules translate between repoctx's
protocol and the specific conventions of each coding-agent harness.
"""

from pathlib import Path
from typing import Any

from repoctx.harness.claude_code import (
    AGENTS_SECTION_HEADER,
    install_claude_code,
    render_agents_section,
)
from repoctx.harness.codex import install_codex
from repoctx.harness.cursor import install_cursor


def install_all(repo_root: str | Path = ".", *, scaffold_authority: bool = True) -> dict[str, Any]:
    """One-shot install for every supported harness + optional scaffold.

    Mirrors GitNexus's ``analyze`` UX: a single command writes AGENTS.md
    sections, registers MCP entries for Claude Code / Cursor / Codex, and
    (optionally) scaffolds the ``contracts/`` + ``docs/architecture/`` +
    ``examples/`` starter layout. Each step is independently idempotent.
    """
    results: dict[str, Any] = {}
    errors: dict[str, str] = {}

    def _try(name: str, fn) -> None:
        try:
            results[name] = fn().to_dict()
        except Exception as exc:  # pragma: no cover - defensive
            errors[name] = f"{type(exc).__name__}: {exc}"

    _try("claude_code", lambda: install_claude_code(repo_root=repo_root))
    _try("cursor", lambda: install_cursor(repo_root=repo_root))
    _try("codex", lambda: install_codex(repo_root=repo_root))

    if scaffold_authority:
        from repoctx.authority.scaffold import init_authority

        _try("authority_scaffold", lambda: init_authority(repo_root=repo_root))

    return {"installed": results, "errors": errors}


__all__ = [
    "AGENTS_SECTION_HEADER",
    "install_all",
    "install_claude_code",
    "install_codex",
    "install_cursor",
    "render_agents_section",
]
