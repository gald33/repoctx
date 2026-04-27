"""Mock artifact adapter proving the retrieval core works for non-repo records.

This test demonstrates that a future registry adapter can index and retrieve
structured artifact summaries (job kinds, service descriptions, schema
summaries, etc.) using the same retrieval core as repo records, with
metadata filtering and cross-namespace support.
"""

from __future__ import annotations

import hashlib

import pytest

numpy = pytest.importorskip("numpy")

from repoctx.core import RecordStore
from repoctx.record import (
    MetadataFilter,
    RetrievableRecord,
    RetrievalQuery,
)


class FakeProvider:
    """Deterministic embedding provider for test isolation."""

    def __init__(self, dim: int = 32) -> None:
        self._dim = dim

    @property
    def dimension(self) -> int:
        return self._dim

    def encode_texts(self, texts: list[str], *, show_progress: bool = True) -> numpy.ndarray:
        return numpy.array([self._vec(t) for t in texts], dtype=numpy.float32)

    def encode_query(self, text: str) -> numpy.ndarray:
        return self._vec(text)

    def _vec(self, text: str) -> numpy.ndarray:
        seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)
        rng = numpy.random.RandomState(seed)
        v = rng.rand(self._dim).astype(numpy.float32)
        return v / numpy.linalg.norm(v)


REGISTRY_NAMESPACE = "registry"


def _mock_artifact_records() -> list[RetrievableRecord]:
    """Simulate what a Lucille registry adapter would produce."""
    return [
        RetrievableRecord(
            id="artifact:image-classifier-v2",
            text=(
                "Image classifier v2 artifact. Trained on ImageNet. "
                "Supports batch inference with ONNX runtime. "
                "Input: 224x224 RGB images. Output: top-5 class probabilities."
            ),
            record_type="artifact_summary",
            namespace=REGISTRY_NAMESPACE,
            metadata={
                "language": "python",
                "visibility": "public",
                "artifact_kind": "model",
            },
        ),
        RetrievableRecord(
            id="artifact:text-embedding-small",
            text=(
                "Text embedding model. 384-dimensional output. "
                "Optimized for retrieval and similarity search. "
                "Multilingual support with 100+ languages."
            ),
            record_type="artifact_summary",
            namespace=REGISTRY_NAMESPACE,
            metadata={
                "language": "python",
                "visibility": "public",
                "artifact_kind": "model",
            },
        ),
        RetrievableRecord(
            id="service:auth-gateway",
            text=(
                "Authentication gateway service. Handles OAuth2 flows, "
                "JWT token validation, and session management. "
                "Exposes REST and gRPC endpoints."
            ),
            record_type="service_summary",
            namespace=REGISTRY_NAMESPACE,
            metadata={
                "language": "go",
                "visibility": "internal",
                "artifact_kind": "service",
            },
        ),
        RetrievableRecord(
            id="schema:user-profile",
            text=(
                "User profile schema. Fields: id (uuid), email (string), "
                "display_name (string), avatar_url (string), created_at (timestamp). "
                "Used by auth-gateway and user-service."
            ),
            record_type="schema_summary",
            namespace=REGISTRY_NAMESPACE,
            metadata={
                "language": "sql",
                "visibility": "internal",
                "artifact_kind": "schema",
            },
        ),
    ]


# -- basic indexing and retrieval -------------------------------------------


def test_index_and_query_artifact_records() -> None:
    store = RecordStore()
    provider = FakeProvider()
    records = _mock_artifact_records()
    store.index_records(records, provider)

    assert len(store) == 4
    assert store.namespaces == {REGISTRY_NAMESPACE}
    assert "artifact_summary" in store.record_types
    assert "service_summary" in store.record_types
    assert "schema_summary" in store.record_types

    results = store.query(
        RetrievalQuery(text="image classification model", top_k=2),
        provider,
    )
    assert len(results) >= 1
    assert all(r.namespace == REGISTRY_NAMESPACE for r in results)


# -- namespace filtering ----------------------------------------------------


def test_query_isolates_namespaces() -> None:
    store = RecordStore()
    provider = FakeProvider()

    repo_records = [
        RetrievableRecord(
            id="src/model.py",
            text="ML model training code with PyTorch",
            record_type="code_chunk",
            namespace="repo",
            metadata={"language": "python"},
        ),
    ]
    store.index_records(repo_records + _mock_artifact_records(), provider)

    registry_results = store.query(
        RetrievalQuery(text="model", namespace=REGISTRY_NAMESPACE, top_k=10),
        provider,
    )
    assert all(r.namespace == REGISTRY_NAMESPACE for r in registry_results)

    repo_results = store.query(
        RetrievalQuery(text="model", namespace="repo", top_k=10),
        provider,
    )
    assert all(r.namespace == "repo" for r in repo_results)


# -- record type filtering -------------------------------------------------


def test_filter_by_record_type() -> None:
    store = RecordStore()
    provider = FakeProvider()
    store.index_records(_mock_artifact_records(), provider)

    results = store.query(
        RetrievalQuery(
            text="service endpoint",
            record_types=["service_summary"],
            top_k=10,
        ),
        provider,
    )
    assert all(r.record_type == "service_summary" for r in results)
    assert len(results) >= 1


# -- metadata filtering ----------------------------------------------------


def test_filter_by_visibility() -> None:
    store = RecordStore()
    provider = FakeProvider()
    store.index_records(_mock_artifact_records(), provider)

    results = store.query(
        RetrievalQuery(
            text="authentication",
            metadata_filters=[MetadataFilter(key="visibility", values=["internal"])],
            top_k=10,
        ),
        provider,
    )
    assert all(r.metadata.get("visibility") == "internal" for r in results)


def test_filter_by_language() -> None:
    store = RecordStore()
    provider = FakeProvider()
    store.index_records(_mock_artifact_records(), provider)

    results = store.query(
        RetrievalQuery(
            text="embedding model",
            metadata_filters=[MetadataFilter(key="language", values=["python"])],
            top_k=10,
        ),
        provider,
    )
    assert all(r.metadata.get("language") == "python" for r in results)


def test_filter_by_artifact_kind() -> None:
    store = RecordStore()
    provider = FakeProvider()
    store.index_records(_mock_artifact_records(), provider)

    results = store.query(
        RetrievalQuery(
            text="data structure",
            metadata_filters=[MetadataFilter(key="artifact_kind", values=["schema"])],
            top_k=10,
        ),
        provider,
    )
    assert all(r.metadata.get("artifact_kind") == "schema" for r in results)
    assert len(results) >= 1


# -- cross-namespace retrieval (mixed repo + registry) ----------------------


def test_cross_namespace_retrieval_without_filter() -> None:
    """When no namespace filter is set, results can come from any namespace."""
    store = RecordStore()
    provider = FakeProvider()

    repo_records = [
        RetrievableRecord(
            id="src/auth.py",
            text="Authentication module with OAuth2 support",
            record_type="code_chunk",
            namespace="repo",
            metadata={"language": "python"},
        ),
    ]
    store.index_records(repo_records + _mock_artifact_records(), provider)

    results = store.query(
        RetrievalQuery(text="OAuth authentication", top_k=10),
        provider,
    )
    namespaces_returned = {r.namespace for r in results}
    assert len(namespaces_returned) >= 1


# -- persistence of artifact records ----------------------------------------


def test_artifact_store_persists(tmp_path) -> None:
    store = RecordStore()
    provider = FakeProvider()
    store.index_records(_mock_artifact_records(), provider)
    store.save(tmp_path / "artifact_idx")

    loaded = RecordStore.load(tmp_path / "artifact_idx")
    assert len(loaded) == 4
    assert loaded.namespaces == {REGISTRY_NAMESPACE}
    assert "artifact_summary" in loaded.record_types

    results = loaded.query(
        RetrievalQuery(text="image model", top_k=2),
        provider,
    )
    assert len(results) >= 1
