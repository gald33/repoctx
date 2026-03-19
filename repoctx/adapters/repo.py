"""Repository adapter – bridges the existing repo scanner to the generic record model.

This adapter:
- Traverses a repository using :func:`repoctx.scanner.scan_repository`
- Converts each :class:`FileRecord` into a :class:`RetrievableRecord`
- Preserves all repo-specific metadata (path, language, kind, doc_score)
  inside the record's metadata map
- Provides helpers to build a :class:`RecordStore` from a repository
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from repoctx.config import DEFAULT_CONFIG, RepoCtxConfig
from repoctx.models import FileRecord, RepositoryIndex
from repoctx.record import RetrievableRecord
from repoctx.scanner import scan_repository

if TYPE_CHECKING:
    from repoctx.core import EmbeddingProvider, RecordStore

REPO_NAMESPACE = "repo"

EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".md": "markdown",
    ".mdc": "markdown",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
}


def file_record_to_retrievable(record: FileRecord, repo_root: Path) -> RetrievableRecord:
    """Convert a repo :class:`FileRecord` to a generic :class:`RetrievableRecord`."""
    parts = PurePosixPath(record.path).parts
    module = "/".join(parts[:-1]) if len(parts) > 1 else ""

    lines = [f"file: {record.path}", f"kind: {record.kind}"]
    if module:
        lines.append(f"module: {module}")
    lines.append("")
    content = record.content[:8000] if record.content else ""
    lines.append(content)
    text = "\n".join(lines)

    record_type = _kind_to_record_type(record.kind)
    language = EXTENSION_TO_LANGUAGE.get(record.extension, "")

    metadata: dict[str, object] = {
        "path": record.path,
        "extension": record.extension,
        "kind": record.kind,
        "language": language,
    }
    if record.doc_score:
        metadata["doc_score"] = record.doc_score
    if module:
        metadata["module"] = module

    return RetrievableRecord(
        id=record.path,
        text=text,
        record_type=record_type,
        namespace=REPO_NAMESPACE,
        metadata=metadata,
        parent_id=None,
    )


def scan_to_records(
    repo_root: str | Path,
    config: RepoCtxConfig = DEFAULT_CONFIG,
) -> tuple[RepositoryIndex, list[RetrievableRecord]]:
    """Scan a repository and return both the classic index and generic records.

    The :class:`RepositoryIndex` is returned so callers that still need the
    legacy retriever (heuristic ranking, dependency graph, etc.) can use it.
    """
    root = Path(repo_root).resolve()
    index = scan_repository(root, config=config)
    records = [
        file_record_to_retrievable(fr, root)
        for fr in index.records.values()
    ]
    return index, records


def build_record_store(
    repo_root: str | Path,
    provider: EmbeddingProvider,
    config: RepoCtxConfig = DEFAULT_CONFIG,
    *,
    show_progress: bool = True,
) -> RecordStore:
    """Scan a repo and return a populated :class:`RecordStore`."""
    from repoctx.core import RecordStore

    _, records = scan_to_records(repo_root, config)
    store = RecordStore()
    store.index_records(records, provider, show_progress=show_progress)
    return store


def _kind_to_record_type(kind: str) -> str:
    return {
        "code": "code_chunk",
        "doc": "doc_chunk",
        "test": "test_chunk",
        "config": "config_chunk",
    }.get(kind, "file_chunk")
