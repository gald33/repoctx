"""Repository adapter that translates repository files into generic records."""

from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(slots=True)
class RepoRecordProducer:
    """Build retrievable records from a repository checkout."""

    repo_root: Path
    config: RepoCtxConfig = DEFAULT_CONFIG

    def scan(self) -> RepositoryIndex:
        return scan_repository(self.repo_root, config=self.config)

    def build_records(self) -> list[RetrievableRecord]:
        index = self.scan()
        return [file_record_to_retrievable(record, self.repo_root) for record in index.records.values()]

    def scan_to_records(self) -> tuple[RepositoryIndex, list[RetrievableRecord]]:
        index = self.scan()
        return index, [file_record_to_retrievable(record, self.repo_root) for record in index.records.values()]


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
    producer = RepoRecordProducer(Path(repo_root).resolve(), config=config)
    return producer.scan_to_records()


def build_record_store(
    repo_root: str | Path,
    provider: EmbeddingProvider,
    config: RepoCtxConfig = DEFAULT_CONFIG,
    *,
    show_progress: bool = True,
) -> RecordStore:
    """Scan a repo and return a populated :class:`RecordStore`."""
    from repoctx.core import RecordStore

    producer = RepoRecordProducer(Path(repo_root).resolve(), config=config)
    store = RecordStore()
    store.index_producer(producer, provider, show_progress=show_progress)
    return store


def _kind_to_record_type(kind: str) -> str:
    return {
        "code": "code_chunk",
        "doc": "doc_chunk",
        "test": "test_chunk",
        "config": "config_chunk",
    }.get(kind, "file_chunk")
