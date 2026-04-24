"""Tests for the generic record model and query types."""

from repoctx.record import MetadataFilter, RetrievableRecord, RetrievalQuery, RetrievalResult


def test_retrievable_record_defaults() -> None:
    r = RetrievableRecord(id="r1", text="hello", record_type="code_chunk")
    assert r.id == "r1"
    assert r.namespace == "default"
    assert r.metadata == {}
    assert r.parent_id is None


def test_retrievable_record_with_metadata() -> None:
    r = RetrievableRecord(
        id="art-42",
        text="Artifact summary text",
        record_type="artifact_summary",
        namespace="registry",
        metadata={"language": "python", "visibility": "public"},
        parent_id="art-parent",
    )
    assert r.namespace == "registry"
    assert r.record_type == "artifact_summary"
    assert r.metadata["language"] == "python"
    assert r.parent_id == "art-parent"
    assert r.source_namespace == "registry"
    assert r.canonical_text == "Artifact summary text"


def test_metadata_filter_matches() -> None:
    f = MetadataFilter(key="kind", values=["code", "test"])
    assert f.matches({"kind": "code"})
    assert f.matches({"kind": "test"})
    assert not f.matches({"kind": "doc"})
    assert not f.matches({})


def test_metadata_filter_multiple_values() -> None:
    f = MetadataFilter(key="language", values=["python", "typescript"])
    assert f.matches({"language": "python", "other": 1})
    assert not f.matches({"language": "rust"})


def test_metadata_filter_prefix_operator() -> None:
    f = MetadataFilter(key="path", values=["src/auth"], operator="prefix")
    assert f.matches({"path": "src/auth/login.py"})
    assert not f.matches({"path": "tests/test_login.py"})


def test_metadata_filter_exists_operator() -> None:
    f = MetadataFilter(key="path", operator="exists")
    assert f.matches({"path": "src/app.py"})
    assert not f.matches({"language": "python"})


def test_retrieval_query_defaults() -> None:
    q = RetrievalQuery(text="find auth code")
    assert q.top_k == 10
    assert q.namespace is None
    assert q.namespaces is None
    assert q.record_types is None
    assert q.metadata_filters is None
    assert q.min_score == 0.0


def test_retrieval_query_with_filters() -> None:
    q = RetrievalQuery(
        text="auth",
        top_k=5,
        namespace="repo",
        record_types=["code_chunk"],
        metadata_filters=[MetadataFilter(key="language", values=["python"])],
        min_score=0.3,
    )
    assert q.namespace == "repo"
    assert q.record_types == ["code_chunk"]
    assert len(q.metadata_filters) == 1
    assert q.min_score == 0.3
    assert q.selected_namespaces() == ["repo"]


def test_retrieval_result_fields() -> None:
    r = RetrievalResult(
        record_id="r1",
        score=0.85,
        record_type="code_chunk",
        namespace="repo",
        metadata={"path": "src/auth.py"},
    )
    assert r.record_id == "r1"
    assert r.score == 0.85
    assert r.namespace == "repo"
