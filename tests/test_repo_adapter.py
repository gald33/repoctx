"""Tests for the repository adapter layer."""

from __future__ import annotations

from pathlib import Path

from repoctx.adapters.repo import (
    REPO_NAMESPACE,
    RepoRecordProducer,
    build_record_store,
    file_record_to_retrievable,
    scan_to_records,
)
from repoctx.core import RecordStore
from repoctx.models import FileRecord

import pytest

numpy = pytest.importorskip("numpy")


class FakeProvider:
    def __init__(self, dim: int = 8) -> None:
        self._dim = dim

    @property
    def dimension(self) -> int:
        return self._dim

    def encode_texts(self, texts: list[str], *, show_progress: bool = True) -> numpy.ndarray:
        return numpy.ones((len(texts), self._dim), dtype=numpy.float32)

    def encode_query(self, text: str) -> numpy.ndarray:
        return numpy.ones((self._dim,), dtype=numpy.float32)


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# -- file_record_to_retrievable -------------------------------------------


def test_code_record_conversion() -> None:
    record = FileRecord(
        path="src/auth/login.py",
        absolute_path=Path("/repo/src/auth/login.py"),
        extension=".py",
        kind="code",
        content="def login():\n    pass\n",
    )
    rr = file_record_to_retrievable(record, Path("/repo"))

    assert rr.id == "src/auth/login.py"
    assert rr.record_type == "code_chunk"
    assert rr.namespace == REPO_NAMESPACE
    assert "file: src/auth/login.py" in rr.text
    assert "kind: code" in rr.text
    assert "module: src/auth" in rr.text
    assert "def login" in rr.text
    assert rr.metadata["path"] == "src/auth/login.py"
    assert rr.metadata["language"] == "python"
    assert rr.metadata["kind"] == "code"


def test_doc_record_conversion() -> None:
    record = FileRecord(
        path="AGENTS.md",
        absolute_path=Path("/repo/AGENTS.md"),
        extension=".md",
        kind="doc",
        content="# Agent guidance",
        doc_score=12.0,
    )
    rr = file_record_to_retrievable(record, Path("/repo"))

    assert rr.record_type == "doc_chunk"
    assert rr.metadata["doc_score"] == 12.0
    assert rr.metadata["language"] == "markdown"
    assert "module" not in rr.metadata


def test_test_record_conversion() -> None:
    record = FileRecord(
        path="tests/test_login.py",
        absolute_path=Path("/repo/tests/test_login.py"),
        extension=".py",
        kind="test",
        content="def test_login():\n    assert True\n",
    )
    rr = file_record_to_retrievable(record, Path("/repo"))
    assert rr.record_type == "test_chunk"
    assert rr.metadata["kind"] == "test"


def test_config_record_conversion() -> None:
    record = FileRecord(
        path="config.json",
        absolute_path=Path("/repo/config.json"),
        extension=".json",
        kind="config",
        content='{"key": "value"}',
    )
    rr = file_record_to_retrievable(record, Path("/repo"))
    assert rr.record_type == "config_chunk"
    assert rr.metadata["language"] == "json"


# -- scan_to_records -------------------------------------------------------


def test_scan_to_records_from_repo(tmp_path: Path) -> None:
    write_file(tmp_path / "README.md", "# Hello\n")
    write_file(tmp_path / "src" / "app.py", "def main():\n    pass\n")
    write_file(tmp_path / "tests" / "test_app.py", "def test_main():\n    assert True\n")

    index, records = scan_to_records(tmp_path)

    assert len(records) == len(index.records)
    assert all(r.namespace == REPO_NAMESPACE for r in records)

    record_types = {r.record_type for r in records}
    assert "code_chunk" in record_types
    assert "doc_chunk" in record_types
    assert "test_chunk" in record_types

    ids = {r.id for r in records}
    assert "README.md" in ids
    assert "src/app.py" in ids
    assert "tests/test_app.py" in ids


def test_scan_to_records_preserves_index_compatibility(tmp_path: Path) -> None:
    """The classic RepositoryIndex returned by scan_to_records should work
    with the existing retriever pipeline unchanged."""
    write_file(tmp_path / "AGENTS.md", "# Guidance\n")
    write_file(tmp_path / "src" / "webhook.py", "def handle():\n    pass\n")

    index, records = scan_to_records(tmp_path)

    assert index.root == tmp_path.resolve()
    assert len(index.docs) >= 1
    assert len(index.code_files) >= 1

    from repoctx.graph import build_dependency_graph
    from repoctx.retriever import get_task_context_data

    graph = build_dependency_graph(index)
    result = get_task_context_data(
        task="webhook handler",
        index=index,
        graph=graph,
    )
    assert result.summary


def test_repo_record_producer_builds_records(tmp_path: Path) -> None:
    write_file(tmp_path / "README.md", "# Docs\n")
    write_file(tmp_path / "src" / "worker.py", "def run():\n    return 1\n")

    producer = RepoRecordProducer(tmp_path)
    records = producer.build_records()

    assert records
    assert all(record.namespace == REPO_NAMESPACE for record in records)
    assert {record.record_type for record in records} >= {"doc_chunk", "code_chunk"}


def test_build_record_store_uses_shared_core(tmp_path: Path) -> None:
    write_file(tmp_path / "src" / "auth.py", "def authenticate():\n    return True\n")
    write_file(tmp_path / "README.md", "# Auth service\n")

    store = build_record_store(tmp_path, FakeProvider())

    assert isinstance(store, RecordStore)
    assert len(store) == 2
    assert store.namespaces == {REPO_NAMESPACE}
