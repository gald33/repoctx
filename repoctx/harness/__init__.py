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


def install_all(
    repo_root: str | Path = ".",
    *,
    scaffold_authority: bool = True,
    build_index: bool | None = None,
) -> dict[str, Any]:
    """One-shot install for every supported harness + optional scaffold.

    Mirrors GitNexus's ``analyze`` UX: a single command writes AGENTS.md
    sections, registers MCP entries for Claude Code / Cursor / Codex, and
    (optionally) scaffolds the ``contracts/`` + ``docs/architecture/`` +
    ``examples/`` starter layout. Each step is independently idempotent.

    ``build_index`` controls whether the embedding index is built as part of
    install. ``None`` (default) means *auto*: build iff the ``[embeddings]``
    extras are importable. ``True`` forces a build (and surfaces the
    ImportError if extras are missing). ``False`` skips it.
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

    index_status = _maybe_build_index(repo_root, build_index, errors)
    if index_status is not None:
        results["embedding_index"] = index_status

    return {"installed": results, "errors": errors}


def _maybe_build_index(
    repo_root: str | Path,
    build_index: bool | None,
    errors: dict[str, str],
) -> dict[str, Any] | None:
    """Build the embedding index per the ``build_index`` tri-state.

    Returns the status dict to record under ``installed.embedding_index``, or
    ``None`` to omit the key entirely (when ``build_index=False``).
    """
    if build_index is False:
        return None

    from repoctx.embeddings import HAS_EMBEDDINGS

    if not HAS_EMBEDDINGS:
        if build_index is True:
            errors["embedding_index"] = (
                "ImportError: sentence-transformers is required. "
                "Install with: pip install 'repoctx-mcp[embeddings]'"
            )
            return None
        return {"status": "skipped", "reason": "embeddings extras not installed"}

    from repoctx.config import DEFAULT_EMBEDDING_CONFIG
    from repoctx.embeddings import build_index as _build_index

    root = Path(repo_root).resolve()
    try:
        record_store = _build_index(root)
        emb_dir = root / DEFAULT_EMBEDDING_CONFIG.index_dir / "embeddings"
        record_store.save(emb_dir)
    except Exception as exc:  # pragma: no cover - defensive
        errors["embedding_index"] = f"{type(exc).__name__}: {exc}"
        return None
    return {
        "status": "built",
        "files": len(record_store),
        "index_dir": str(emb_dir),
    }


__all__ = [
    "AGENTS_SECTION_HEADER",
    "install_all",
    "install_claude_code",
    "install_codex",
    "install_cursor",
    "render_agents_section",
]
