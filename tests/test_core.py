"""Tests for the domain-agnostic retrieval core."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

numpy = pytest.importorskip("numpy")

from repoctx.core import RecordStore, _record_content_hash
from repoctx.record import (
    MetadataFilter,
    RetrievableRecord,
    RetrievalQuery,
    RetrievalResult,
)


# -- Fake embedding provider for testing ------------------------------------


class FakeProvider:
    """Deterministic bag-of-words provider.

    Each lowercased word maps to a fixed random unit vector (seeded from a
    stable ``sha256`` digest — not Python's built-in ``hash()``, which is
    randomised per-process via ``PYTHONHASHSEED`` and would make results
    non-reproducible across runs). A text's embedding is the L2-normalised
    sum of its word vectors, so texts sharing words have positive cosine
    similarity — close enough to real embedding behaviour for filter tests.
    """

    def __init__(self, dim: int = 32) -> None:
        self._dim = dim

    @property
    def dimension(self) -> int:
        return self._dim

    def encode_texts(self, texts: list[str], *, show_progress: bool = True) -> numpy.ndarray:
        return numpy.array([self._text_to_vec(t) for t in texts], dtype=numpy.float32)

    def encode_query(self, text: str) -> numpy.ndarray:
        return self._text_to_vec(text)

    def _word_to_vec(self, word: str) -> numpy.ndarray:
        digest = hashlib.sha256(word.encode("utf-8")).digest()
        seed = int.from_bytes(digest[:4], "big")
        rng = numpy.random.RandomState(seed)
        vec = rng.randn(self._dim).astype(numpy.float32)
        return vec / numpy.linalg.norm(vec)

    def _text_to_vec(self, text: str) -> numpy.ndarray:
        words = text.lower().split() or [""]
        summed = numpy.sum([self._word_to_vec(w) for w in words], axis=0)
        norm = numpy.linalg.norm(summed)
        if norm == 0:
            return summed.astype(numpy.float32)
        return (summed / norm).astype(numpy.float32)


# -- helpers ----------------------------------------------------------------


def _make_records(n: int = 5, namespace: str = "default") -> list[RetrievableRecord]:
    return [
        RetrievableRecord(
            id=f"record_{i}",
            text=f"Content for record {i} about topic {chr(65 + i)}",
            record_type="code_chunk" if i % 2 == 0 else "doc_chunk",
            namespace=namespace,
            metadata={"language": "python" if i % 2 == 0 else "markdown", "index": i},
        )
        for i in range(n)
    ]


# -- RecordStore indexing ---------------------------------------------------


def test_index_records_populates_store() -> None:
    store = RecordStore()
    records = _make_records(3)
    provider = FakeProvider()
    store.index_records(records, provider)

    assert len(store) == 3
    assert store.dimension == 32
    assert store.namespaces == {"default"}
    assert "code_chunk" in store.record_types


def test_index_records_empty_list() -> None:
    store = RecordStore()
    store.index_records([], FakeProvider())
    assert len(store) == 0


def test_add_record_upsert() -> None:
    store = RecordStore()
    provider = FakeProvider()
    records = _make_records(2)
    store.index_records(records, provider)
    assert len(store) == 2

    new_record = RetrievableRecord(
        id="record_0",
        text="Updated content for record 0",
        record_type="code_chunk",
        namespace="default",
    )
    store.add_record(new_record, provider)
    assert len(store) == 2


def test_add_record_to_empty_store() -> None:
    store = RecordStore()
    provider = FakeProvider()
    record = RetrievableRecord(id="first", text="Hello world", record_type="doc_chunk")
    store.add_record(record, provider)
    assert len(store) == 1


# -- querying ---------------------------------------------------------------


def test_query_returns_results() -> None:
    store = RecordStore()
    provider = FakeProvider()
    store.index_records(_make_records(5), provider)

    results = store.query(
        RetrievalQuery(text="Content for record 0 about topic A", top_k=3),
        provider,
    )
    assert len(results) <= 3
    assert all(isinstance(r, RetrievalResult) for r in results)
    assert results[0].score >= results[-1].score


def test_query_empty_store() -> None:
    store = RecordStore()
    results = store.query(
        RetrievalQuery(text="anything"),
        FakeProvider(),
    )
    assert results == []


def test_query_respects_top_k() -> None:
    store = RecordStore()
    provider = FakeProvider()
    store.index_records(_make_records(10), provider)

    results = store.query(RetrievalQuery(text="topic", top_k=3), provider)
    assert len(results) <= 3


def test_query_filters_by_namespace() -> None:
    store = RecordStore()
    provider = FakeProvider()

    repo_records = _make_records(3, namespace="repo")
    registry_records = [
        RetrievableRecord(
            id=f"artifact_{i}",
            text=f"Artifact {i} summary",
            record_type="artifact_summary",
            namespace="registry",
            metadata={"language": "python"},
        )
        for i in range(3)
    ]
    store.index_records(repo_records + registry_records, provider)

    results = store.query(
        RetrievalQuery(text="summary", namespace="registry", top_k=10),
        provider,
    )
    assert all(r.namespace == "registry" for r in results)

    results = store.query(
        RetrievalQuery(text="content", namespace="repo", top_k=10),
        provider,
    )
    assert all(r.namespace == "repo" for r in results)


def test_query_filters_by_record_type() -> None:
    store = RecordStore()
    provider = FakeProvider()
    store.index_records(_make_records(6), provider)

    results = store.query(
        RetrievalQuery(text="topic", record_types=["doc_chunk"], top_k=10),
        provider,
    )
    assert all(r.record_type == "doc_chunk" for r in results)


def test_query_filters_by_metadata() -> None:
    store = RecordStore()
    provider = FakeProvider()
    store.index_records(_make_records(6), provider)

    results = store.query(
        RetrievalQuery(
            text="topic",
            metadata_filters=[MetadataFilter(key="language", values=["python"])],
            top_k=10,
        ),
        provider,
    )
    assert all(r.metadata.get("language") == "python" for r in results)


def test_query_min_score_filtering() -> None:
    store = RecordStore()
    provider = FakeProvider()
    store.index_records(_make_records(5), provider)

    results = store.query(
        RetrievalQuery(text="topic", min_score=0.99, top_k=10),
        provider,
    )
    assert all(r.score >= 0.99 for r in results)


def test_query_combined_filters() -> None:
    store = RecordStore()
    provider = FakeProvider()

    records = _make_records(4, namespace="repo") + [
        RetrievableRecord(
            id="artifact_0",
            text="Python artifact summary",
            record_type="artifact_summary",
            namespace="registry",
            metadata={"language": "python"},
        ),
        RetrievableRecord(
            id="artifact_1",
            text="JavaScript service",
            record_type="service_summary",
            namespace="registry",
            metadata={"language": "javascript"},
        ),
    ]
    store.index_records(records, provider)

    results = store.query(
        RetrievalQuery(
            text="Python",
            namespace="registry",
            record_types=["artifact_summary"],
            metadata_filters=[MetadataFilter(key="language", values=["python"])],
            top_k=10,
        ),
        provider,
    )
    assert len(results) >= 1
    assert all(r.namespace == "registry" for r in results)
    assert all(r.record_type == "artifact_summary" for r in results)


# -- persistence round-trip -------------------------------------------------


def test_save_and_load(tmp_path: Path) -> None:
    store = RecordStore()
    provider = FakeProvider()
    records = _make_records(3, namespace="repo")
    store.index_records(records, provider)

    store.save(tmp_path / "idx")
    loaded = RecordStore.load(tmp_path / "idx")

    assert len(loaded) == 3
    assert loaded.namespaces == {"repo"}
    assert "code_chunk" in loaded.record_types


def test_save_empty_store_raises(tmp_path: Path) -> None:
    store = RecordStore()
    with pytest.raises(ValueError, match="empty"):
        store.save(tmp_path / "idx")


# -- multi-namespace introspection ------------------------------------------


def test_namespaces_and_record_types() -> None:
    store = RecordStore()
    provider = FakeProvider()
    records = [
        RetrievableRecord(id="r1", text="code", record_type="code_chunk", namespace="repo"),
        RetrievableRecord(id="r2", text="art", record_type="artifact_summary", namespace="registry"),
    ]
    store.index_records(records, provider)
    assert store.namespaces == {"repo", "registry"}
    assert store.record_types == {"code_chunk", "artifact_summary"}


# -- content hash -----------------------------------------------------------


def test_record_content_hash_deterministic() -> None:
    h1 = _record_content_hash("hello")
    h2 = _record_content_hash("hello")
    assert h1 == h2
    assert len(h1) == 16
    assert _record_content_hash("a") != _record_content_hash("b")
