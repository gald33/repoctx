"""Tests for the Lucille registry adapter.

Covers record construction, stable IDs, canonical text, metadata,
filtering, versioning, mixed-store retrieval, and persistence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

numpy = pytest.importorskip("numpy")

from repoctx.adapters.registry import (
    DEFAULT_REGISTRY_CONFIG,
    LUCILLE_NAMESPACE,
    ImplementationSnapshot,
    JobKindSnapshot,
    LucilleRegistryAdapterConfig,
    LucilleRegistryRecordProducer,
    RegistrySnapshot,
    SchemaSnapshot,
    ServiceSnapshot,
    implementation_id,
    implementation_to_record,
    job_kind_id,
    job_kind_to_record,
    schema_id,
    schema_to_record,
    service_id,
    service_to_record,
)
from repoctx.core import RecordStore
from repoctx.record import MetadataFilter, RetrievableRecord, RetrievalQuery


# ---------------------------------------------------------------------------
# Fake embedding provider
# ---------------------------------------------------------------------------


class FakeProvider:
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
        rng = numpy.random.RandomState(hash(text) % (2**31))
        v = rng.randn(self._dim).astype(numpy.float32)
        return v / numpy.linalg.norm(v)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _sample_job_kind(**overrides: object) -> JobKindSnapshot:
    defaults: dict = dict(
        job_kind="etl.extract",
        title="ETL Extract",
        summary="Extracts data from upstream sources into staging.",
        purpose="Pull raw data from APIs and databases for downstream processing.",
        inputs_summary="Source credentials, extraction config",
        outputs_summary="Staged raw data files",
        execution_model="batch",
        integrations=["postgres-source", "s3-staging"],
        services=["data-pipeline-svc"],
        implementations=["extract-pg-v1", "extract-api-v2"],
        capabilities=["incremental-load", "full-load"],
        constraints=["max-concurrency-4"],
        keywords=["etl", "extract", "data-pipeline", "staging"],
        module_backed=True,
        resource_locks=["staging-bucket"],
        input_schema_id="extraction-config-v1",
        output_schema_id="staged-data-manifest-v1",
        version="1.2.0",
        visibility="public",
        status="active",
        tags=["data", "core"],
        owner="data-team",
        created_at="2025-06-15T10:00:00Z",
        updated_at="2026-01-20T14:30:00Z",
    )
    defaults.update(overrides)
    return JobKindSnapshot(**defaults)


def _sample_implementation(**overrides: object) -> ImplementationSnapshot:
    defaults: dict = dict(
        implementation_id="extract-pg-v1",
        job_kind="etl.extract",
        module_name="lucille.extractors.postgres",
        version="1.0.3",
        title="Postgres Extractor",
        summary="Extracts tables from PostgreSQL using logical replication.",
        runtime="python3.11",
        entrypoint="lucille.extractors.postgres:run",
        dependencies=["psycopg2", "boto3"],
        handles="PostgreSQL sources with CDC support",
        inputs_summary="PG connection string, table list, watermark",
        outputs_summary="Parquet files in S3 staging bucket",
        failure_modes=["connection-timeout", "schema-drift"],
        keywords=["postgres", "cdc", "extractor"],
        signature_hash="abc123def456",
        integrations=["postgres-source"],
        services=["data-pipeline-svc"],
        capabilities=["incremental-load"],
        input_schema_id="pg-extract-config-v1",
        output_schema_id="staged-data-manifest-v1",
        is_default=True,
        visibility="public",
        status="active",
        tags=["data", "postgres"],
        owner="data-team",
        created_at="2025-08-01T12:00:00Z",
        updated_at="2026-02-10T09:00:00Z",
    )
    defaults.update(overrides)
    return ImplementationSnapshot(**defaults)


def _sample_service(**overrides: object) -> ServiceSnapshot:
    defaults: dict = dict(
        service_name="data-pipeline-svc",
        title="Data Pipeline Service",
        summary="Orchestrates ETL pipelines end-to-end.",
        purpose="Coordinate extract, transform, and load steps across the data platform.",
        interfaces=["REST", "gRPC"],
        capabilities=["scheduling", "retry", "monitoring"],
        integrations=["postgres-source", "s3-staging", "redshift-sink"],
        job_kinds=["etl.extract", "etl.transform", "etl.load"],
        operational_notes="Requires IAM role data-pipeline-execution. Scales to 50 concurrent pipelines.",
        keywords=["pipeline", "orchestration", "etl"],
        auth_mode="iam",
        deployment_scope="production",
        version="2.1.0",
        visibility="internal",
        status="active",
        tags=["data", "infrastructure"],
        owner="platform-team",
        created_at="2024-11-01T08:00:00Z",
        updated_at="2026-03-01T16:00:00Z",
    )
    defaults.update(overrides)
    return ServiceSnapshot(**defaults)


def _sample_schema(**overrides: object) -> SchemaSnapshot:
    defaults: dict = dict(
        schema_name="extraction-config",
        schema_family="etl.config",
        schema_version="1.0.0",
        title="Extraction Config Schema",
        summary="Defines the configuration for data extraction jobs.",
        purpose="Provide a validated config structure for extraction job kinds.",
        entity_type="config",
        fields_summary="source_type (string), connection_string (string), tables (list[string]), watermark (datetime), incremental (bool)",
        field_names=["source_type", "connection_string", "tables", "watermark", "incremental"],
        required_fields=["source_type", "connection_string", "tables"],
        related_services=["data-pipeline-svc"],
        related_job_kinds=["etl.extract"],
        compatibility_notes="Backward compatible with v0.9.x configs.",
        compatibility_status="backward_compatible",
        keywords=["schema", "config", "extraction"],
        version="1.0.0",
        visibility="public",
        status="active",
        tags=["data", "schema"],
        owner="data-team",
        created_at="2025-09-01T10:00:00Z",
        updated_at="2026-01-15T11:00:00Z",
    )
    defaults.update(overrides)
    return SchemaSnapshot(**defaults)


def _full_registry_snapshot() -> RegistrySnapshot:
    return RegistrySnapshot(
        job_kinds=[_sample_job_kind()],
        implementations=[
            _sample_implementation(),
            _sample_implementation(
                implementation_id="extract-api-v2",
                module_name="lucille.extractors.rest_api",
                version="2.0.0",
                title="REST API Extractor",
                summary="Extracts data from REST APIs with pagination.",
                runtime="python3.11",
                entrypoint="lucille.extractors.rest_api:run",
                handles="REST APIs with cursor/offset pagination",
                keywords=["rest", "api", "extractor"],
                is_default=False,
            ),
        ],
        services=[_sample_service()],
        schemas=[_sample_schema()],
    )


# ===================================================================
# 1. Record construction
# ===================================================================


class TestJobKindRecordConstruction:
    def test_produces_correct_record_type(self) -> None:
        rec = job_kind_to_record(_sample_job_kind())
        assert rec.record_type == "job_kind_summary"

    def test_namespace(self) -> None:
        rec = job_kind_to_record(_sample_job_kind())
        assert rec.namespace == LUCILLE_NAMESPACE

    def test_text_includes_key_fields(self) -> None:
        rec = job_kind_to_record(_sample_job_kind())
        assert "job_kind: etl.extract" in rec.text
        assert "ETL Extract" in rec.text
        assert "staging" in rec.text.lower()
        assert "batch" in rec.text
        assert "incremental-load" in rec.text


class TestImplementationRecordConstruction:
    def test_produces_correct_record_type(self) -> None:
        rec = implementation_to_record(_sample_implementation())
        assert rec.record_type == "implementation_summary"

    def test_parent_id_references_job_kind(self) -> None:
        rec = implementation_to_record(_sample_implementation())
        assert rec.parent_id == "lucille:job_kind:etl.extract"

    def test_parent_id_none_when_no_job_kind(self) -> None:
        impl = _sample_implementation(job_kind="")
        rec = implementation_to_record(impl)
        assert rec.parent_id is None

    def test_text_includes_key_fields(self) -> None:
        rec = implementation_to_record(_sample_implementation())
        assert "lucille.extractors.postgres" in rec.text
        assert "etl.extract" in rec.text
        assert "python3.11" in rec.text
        assert "psycopg2" in rec.text
        assert "cdc" in rec.text


class TestServiceRecordConstruction:
    def test_produces_correct_record_type(self) -> None:
        rec = service_to_record(_sample_service())
        assert rec.record_type == "service_summary"

    def test_text_includes_key_fields(self) -> None:
        rec = service_to_record(_sample_service())
        assert "data-pipeline-svc" in rec.text
        assert "orchestrat" in rec.text.lower()
        assert "REST" in rec.text
        assert "gRPC" in rec.text
        assert "etl.extract" in rec.text


class TestSchemaRecordConstruction:
    def test_produces_correct_record_type(self) -> None:
        rec = schema_to_record(_sample_schema())
        assert rec.record_type == "schema_summary"

    def test_text_includes_key_fields(self) -> None:
        rec = schema_to_record(_sample_schema())
        assert "extraction-config" in rec.text
        assert "etl.config" in rec.text
        assert "1.0.0" in rec.text
        assert "source_type" in rec.text
        assert "connection_string" in rec.text
        assert "Backward compatible" in rec.text


# ===================================================================
# 2. Stable IDs
# ===================================================================


class TestStableIds:
    def test_job_kind_id_deterministic(self) -> None:
        jk = _sample_job_kind()
        assert job_kind_id(jk) == "lucille:job_kind:etl.extract"
        assert job_kind_id(jk) == job_kind_id(jk)

    def test_implementation_id_with_version(self) -> None:
        impl = _sample_implementation()
        assert implementation_id(impl) == "lucille:implementation:etl.extract:lucille.extractors.postgres@1.0.3"

    def test_implementation_id_without_version(self) -> None:
        impl = _sample_implementation(version="")
        assert implementation_id(impl) == "lucille:implementation:etl.extract:lucille.extractors.postgres"

    def test_implementation_id_falls_back_to_implementation_id(self) -> None:
        impl = _sample_implementation(module_name="")
        assert implementation_id(impl) == "lucille:implementation:etl.extract:extract-pg-v1@1.0.3"

    def test_service_id_deterministic(self) -> None:
        svc = _sample_service()
        assert service_id(svc) == "lucille:service:data-pipeline-svc"

    def test_schema_id_with_version(self) -> None:
        sch = _sample_schema()
        assert schema_id(sch) == "lucille:schema:etl.config:extraction-config@1.0.0"

    def test_schema_id_without_version(self) -> None:
        sch = _sample_schema(schema_version="", version="")
        assert schema_id(sch) == "lucille:schema:etl.config:extraction-config"

    def test_schema_id_default_family(self) -> None:
        sch = _sample_schema(schema_family="")
        assert schema_id(sch).startswith("lucille:schema:default:")

    def test_all_ids_unique_in_snapshot(self) -> None:
        snap = _full_registry_snapshot()
        producer = LucilleRegistryRecordProducer(snapshot=snap)
        records = producer.build_records()
        ids = [r.id for r in records]
        assert len(ids) == len(set(ids))


# ===================================================================
# 3. Canonical text
# ===================================================================


class TestCanonicalText:
    def test_job_kind_text_has_structured_lines(self) -> None:
        rec = job_kind_to_record(_sample_job_kind())
        lines = rec.text.splitlines()
        assert any(l.startswith("job_kind:") for l in lines)
        assert any(l.startswith("title:") for l in lines)
        assert any(l.startswith("summary:") for l in lines)
        assert any(l.startswith("capabilities:") for l in lines)
        assert any(l.startswith("keywords:") for l in lines)

    def test_implementation_text_has_structured_lines(self) -> None:
        rec = implementation_to_record(_sample_implementation())
        lines = rec.text.splitlines()
        assert any(l.startswith("implementation:") for l in lines)
        assert any(l.startswith("job_kind:") for l in lines)
        assert any(l.startswith("module:") for l in lines)
        assert any(l.startswith("runtime:") for l in lines)

    def test_service_text_has_structured_lines(self) -> None:
        rec = service_to_record(_sample_service())
        lines = rec.text.splitlines()
        assert any(l.startswith("service:") for l in lines)
        assert any(l.startswith("interfaces:") for l in lines)
        assert any(l.startswith("capabilities:") for l in lines)

    def test_schema_text_has_structured_lines(self) -> None:
        rec = schema_to_record(_sample_schema())
        lines = rec.text.splitlines()
        assert any(l.startswith("schema:") for l in lines)
        assert any(l.startswith("family:") for l in lines)
        assert any(l.startswith("required_fields:") for l in lines)

    def test_text_respects_max_chars(self) -> None:
        cfg = LucilleRegistryAdapterConfig(max_text_chars=50)
        rec = job_kind_to_record(_sample_job_kind(), cfg)
        assert len(rec.text) <= 50


# ===================================================================
# 4. Metadata correctness
# ===================================================================


class TestMetadataCorrectness:
    def test_common_metadata_present_on_job_kind(self) -> None:
        rec = job_kind_to_record(_sample_job_kind())
        m = rec.metadata
        assert m["source_system"] == "lucille"
        assert m["adapter"] == "registry"
        assert m["artifact_type"] == "job_kind"
        assert m["name"] == "etl.extract"
        assert m["title"] == "ETL Extract"
        assert m["version"] == "1.2.0"
        assert m["version_present"] is True
        assert m["visibility"] == "public"
        assert m["status"] == "active"
        assert "data" in m["tags"]
        assert "etl" in m["keywords"]
        assert m["owner"] == "data-team"
        assert m["created_at"] == "2025-06-15T10:00:00Z"
        assert m["updated_at"] == "2026-01-20T14:30:00Z"

    def test_job_kind_specific_metadata(self) -> None:
        rec = job_kind_to_record(_sample_job_kind())
        m = rec.metadata
        assert m["job_kind"] == "etl.extract"
        assert m["execution_model"] == "batch"
        assert m["module_backed"] is True
        assert "postgres-source" in m["integration_ids"]
        assert "data-pipeline-svc" in m["service_ids"]
        assert "extract-pg-v1" in m["implementation_ids"]
        assert "incremental-load" in m["capabilities"]
        assert "staging-bucket" in m["resource_locks"]
        assert m["input_schema_id"] == "extraction-config-v1"
        assert m["output_schema_id"] == "staged-data-manifest-v1"

    def test_implementation_specific_metadata(self) -> None:
        rec = implementation_to_record(_sample_implementation())
        m = rec.metadata
        assert m["implementation_id"] == "extract-pg-v1"
        assert m["job_kind"] == "etl.extract"
        assert m["module_name"] == "lucille.extractors.postgres"
        assert m["runtime"] == "python3.11"
        assert m["entrypoint"] == "lucille.extractors.postgres:run"
        assert m["signature_hash"] == "abc123def456"
        assert "psycopg2" in m["dependency_ids"]
        assert m["is_default"] is True

    def test_service_specific_metadata(self) -> None:
        rec = service_to_record(_sample_service())
        m = rec.metadata
        assert m["service_name"] == "data-pipeline-svc"
        assert "REST" in m["interface_types"]
        assert "gRPC" in m["interface_types"]
        assert m["auth_mode"] == "iam"
        assert m["deployment_scope"] == "production"
        assert "etl.extract" in m["job_kind_ids"]

    def test_schema_specific_metadata(self) -> None:
        rec = schema_to_record(_sample_schema())
        m = rec.metadata
        assert m["schema_name"] == "extraction-config"
        assert m["schema_family"] == "etl.config"
        assert m["schema_version"] == "1.0.0"
        assert m["entity_type"] == "config"
        assert "source_type" in m["field_names"]
        assert "connection_string" in m["required_fields"]
        assert "data-pipeline-svc" in m["related_service_ids"]
        assert "etl.extract" in m["related_job_kind_ids"]
        assert m["compatibility_status"] == "backward_compatible"

    def test_version_present_false_when_no_version(self) -> None:
        jk = _sample_job_kind(version="")
        rec = job_kind_to_record(jk)
        assert rec.metadata["version_present"] is False

    def test_list_metadata_is_pipe_delimited(self) -> None:
        rec = job_kind_to_record(_sample_job_kind())
        m = rec.metadata
        assert isinstance(m["capabilities"], str)
        assert "|" in m["capabilities"]
        assert "incremental-load" in m["capabilities"].split("|")


# ===================================================================
# 5. Filtering behavior
# ===================================================================


class TestFilteringBehavior:
    @pytest.fixture()
    def _loaded_store(self):
        store = RecordStore()
        provider = FakeProvider()
        snap = _full_registry_snapshot()
        producer = LucilleRegistryRecordProducer(snapshot=snap)
        records = producer.build_records()
        store.index_records(records, provider)
        return store, provider

    def test_filter_implementations_by_job_kind(self, _loaded_store) -> None:
        store, provider = _loaded_store
        results = store.query(
            RetrievalQuery(
                text="postgres extractor",
                record_types=["implementation_summary"],
                metadata_filters=[MetadataFilter(key="job_kind", values=["etl.extract"], operator="equals")],
                top_k=10,
                min_score=-1.0,
            ),
            provider,
        )
        assert len(results) >= 1
        assert all(r.record_type == "implementation_summary" for r in results)
        assert all(r.metadata["job_kind"] == "etl.extract" for r in results)

    def test_filter_schemas_by_family(self, _loaded_store) -> None:
        store, provider = _loaded_store
        results = store.query(
            RetrievalQuery(
                text="config schema",
                record_types=["schema_summary"],
                metadata_filters=[MetadataFilter(key="schema_family", values=["etl.config"], operator="equals")],
                top_k=10,
                min_score=-1.0,
            ),
            provider,
        )
        assert len(results) >= 1
        assert all(r.metadata["schema_family"] == "etl.config" for r in results)

    def test_filter_by_status(self, _loaded_store) -> None:
        store, provider = _loaded_store
        results = store.query(
            RetrievalQuery(
                text="data",
                metadata_filters=[MetadataFilter(key="status", values=["active"], operator="equals")],
                top_k=20,
                min_score=-1.0,
            ),
            provider,
        )
        assert len(results) >= 1
        assert all(r.metadata["status"] == "active" for r in results)

    def test_filter_by_visibility(self, _loaded_store) -> None:
        store, provider = _loaded_store
        results = store.query(
            RetrievalQuery(
                text="pipeline",
                metadata_filters=[MetadataFilter(key="visibility", values=["internal"], operator="equals")],
                top_k=10,
                min_score=-1.0,
            ),
            provider,
        )
        assert all(r.metadata["visibility"] == "internal" for r in results)

    def test_prefix_filter_on_module_name(self, _loaded_store) -> None:
        store, provider = _loaded_store
        results = store.query(
            RetrievalQuery(
                text="extractor module",
                metadata_filters=[MetadataFilter(key="module_name", values=["lucille.extractors"], operator="prefix")],
                top_k=10,
                min_score=-1.0,
            ),
            provider,
        )
        assert len(results) >= 1
        for r in results:
            assert str(r.metadata.get("module_name", "")).startswith("lucille.extractors")

    def test_prefix_filter_on_service_name(self, _loaded_store) -> None:
        store, provider = _loaded_store
        results = store.query(
            RetrievalQuery(
                text="service",
                metadata_filters=[MetadataFilter(key="service_name", values=["data-"], operator="prefix")],
                top_k=10,
                min_score=-1.0,
            ),
            provider,
        )
        assert len(results) >= 1
        for r in results:
            assert str(r.metadata.get("service_name", "")).startswith("data-")


# ===================================================================
# 6. Version behavior
# ===================================================================


class TestVersionBehavior:
    def test_selected_versions_produce_distinct_ids(self) -> None:
        impl_v1 = _sample_implementation(version="1.0.0")
        impl_v2 = _sample_implementation(version="2.0.0")
        r1 = implementation_to_record(impl_v1)
        r2 = implementation_to_record(impl_v2)
        assert r1.id != r2.id
        assert "@1.0.0" in r1.id
        assert "@2.0.0" in r2.id

    def test_schema_versions_produce_distinct_ids(self) -> None:
        s1 = _sample_schema(schema_version="1.0.0")
        s2 = _sample_schema(schema_version="2.0.0")
        r1 = schema_to_record(s1)
        r2 = schema_to_record(s2)
        assert r1.id != r2.id

    def test_all_versions_mode_indexes_multiple(self) -> None:
        snap = RegistrySnapshot(
            implementations=[
                _sample_implementation(version="1.0.0"),
                _sample_implementation(
                    implementation_id="extract-pg-v1",
                    module_name="lucille.extractors.postgres",
                    version="1.1.0",
                ),
                _sample_implementation(
                    implementation_id="extract-pg-v1",
                    module_name="lucille.extractors.postgres",
                    version="2.0.0",
                ),
            ],
        )
        cfg = LucilleRegistryAdapterConfig(version_mode="all")
        producer = LucilleRegistryRecordProducer(snapshot=snap, config=cfg)
        records = producer.build_records()
        assert len(records) == 3
        ids = {r.id for r in records}
        assert len(ids) == 3

    def test_caller_controls_which_versions_are_passed(self) -> None:
        snap = RegistrySnapshot(
            implementations=[_sample_implementation(version="1.0.3")],
        )
        producer = LucilleRegistryRecordProducer(snapshot=snap)
        records = producer.build_records()
        assert len(records) == 1
        assert "@1.0.3" in records[0].id


# ===================================================================
# 7. Mixed-store retrieval
# ===================================================================


class TestMixedStoreRetrieval:
    def _mixed_store(self):
        store = RecordStore()
        provider = FakeProvider()
        repo_records = [
            RetrievableRecord(
                id="src/etl/extract.py",
                text="ETL extraction module with postgres and REST API support. Handles data extraction pipeline.",
                record_type="code_chunk",
                namespace="repo",
                metadata={"language": "python", "path": "src/etl/extract.py"},
            ),
            RetrievableRecord(
                id="README.md",
                text="Data pipeline framework documentation. Covers ETL jobs and services.",
                record_type="doc_chunk",
                namespace="repo",
                metadata={"language": "markdown", "path": "README.md"},
            ),
        ]
        snap = _full_registry_snapshot()
        producer = LucilleRegistryRecordProducer(snapshot=snap)
        registry_records = producer.build_records()
        store.index_records(repo_records + registry_records, provider)
        return store, provider

    def test_query_constrained_to_lucille_namespace(self) -> None:
        store, provider = self._mixed_store()
        results = store.query(
            RetrievalQuery(text="extraction", namespace=LUCILLE_NAMESPACE, top_k=10, min_score=-1.0),
            provider,
        )
        assert len(results) >= 1
        assert all(r.namespace == LUCILLE_NAMESPACE for r in results)

    def test_query_constrained_to_repo_namespace(self) -> None:
        store, provider = self._mixed_store()
        results = store.query(
            RetrievalQuery(text="extraction", namespace="repo", top_k=10, min_score=-1.0),
            provider,
        )
        assert len(results) >= 1
        assert all(r.namespace == "repo" for r in results)

    def test_query_constrained_to_implementation_summary(self) -> None:
        store, provider = self._mixed_store()
        results = store.query(
            RetrievalQuery(text="extractor", record_types=["implementation_summary"], top_k=10, min_score=-1.0),
            provider,
        )
        assert all(r.record_type == "implementation_summary" for r in results)

    def test_unconstrained_query_returns_mixed_namespaces(self) -> None:
        store, provider = self._mixed_store()
        results = store.query(
            RetrievalQuery(text="ETL data pipeline extraction", top_k=20, min_score=-1.0),
            provider,
        )
        namespaces = {r.namespace for r in results}
        assert len(namespaces) >= 2
        assert "repo" in namespaces
        assert LUCILLE_NAMESPACE in namespaces


# ===================================================================
# 8. Persistence
# ===================================================================


class TestPersistence:
    def test_save_load_round_trip(self, tmp_path: Path) -> None:
        store = RecordStore()
        provider = FakeProvider()
        snap = _full_registry_snapshot()
        producer = LucilleRegistryRecordProducer(snapshot=snap)
        records = producer.build_records()
        store.index_records(records, provider)

        store.save(tmp_path / "registry_idx")
        loaded = RecordStore.load(tmp_path / "registry_idx")

        assert len(loaded) == len(records)
        assert loaded.namespaces == {LUCILLE_NAMESPACE}
        expected_types = {"job_kind_summary", "implementation_summary", "service_summary", "schema_summary"}
        assert loaded.record_types == expected_types

    def test_loaded_store_preserves_metadata(self, tmp_path: Path) -> None:
        store = RecordStore()
        provider = FakeProvider()
        impl = _sample_implementation()
        rec = implementation_to_record(impl)
        store.index_records([rec], provider)
        store.save(tmp_path / "impl_idx")

        loaded = RecordStore.load(tmp_path / "impl_idx")
        loaded_rec = loaded.get_record(rec.id)
        assert loaded_rec is not None
        assert loaded_rec.namespace == LUCILLE_NAMESPACE
        assert loaded_rec.record_type == "implementation_summary"
        assert loaded_rec.metadata["job_kind"] == "etl.extract"
        assert loaded_rec.metadata["module_name"] == "lucille.extractors.postgres"
        assert loaded_rec.parent_id == "lucille:job_kind:etl.extract"

    def test_loaded_store_supports_queries(self, tmp_path: Path) -> None:
        store = RecordStore()
        provider = FakeProvider()
        snap = _full_registry_snapshot()
        producer = LucilleRegistryRecordProducer(snapshot=snap)
        store.index_records(producer.build_records(), provider)
        store.save(tmp_path / "idx")

        loaded = RecordStore.load(tmp_path / "idx")
        results = loaded.query(
            RetrievalQuery(text="extraction pipeline", top_k=5, min_score=-1.0),
            provider,
        )
        assert len(results) >= 1

    def test_loaded_store_preserves_parent_id_and_embedding_ref(self, tmp_path: Path) -> None:
        store = RecordStore()
        provider = FakeProvider()
        rec = RetrievableRecord(
            id="test:parent-ref",
            text="Test record with parent and embedding ref",
            record_type="implementation_summary",
            namespace=LUCILLE_NAMESPACE,
            metadata={"artifact_type": "implementation"},
            parent_id="lucille:job_kind:test",
            embedding_ref="emb-ref-hash",
        )
        store.index_records([rec], provider)
        store.save(tmp_path / "ref_idx")

        loaded = RecordStore.load(tmp_path / "ref_idx")
        loaded_rec = loaded.get_record("test:parent-ref")
        assert loaded_rec is not None
        assert loaded_rec.parent_id == "lucille:job_kind:test"
        assert loaded_rec.embedding_ref == "emb-ref-hash"


# ===================================================================
# 9. RecordProducer protocol + from_dict helpers
# ===================================================================


class TestRecordProducerProtocol:
    def test_producer_satisfies_protocol(self) -> None:
        from repoctx.core import RecordProducer

        snap = _full_registry_snapshot()
        producer = LucilleRegistryRecordProducer(snapshot=snap)
        assert isinstance(producer, RecordProducer)

    def test_index_producer_integration(self) -> None:
        store = RecordStore()
        provider = FakeProvider()
        snap = _full_registry_snapshot()
        producer = LucilleRegistryRecordProducer(snapshot=snap)
        records = store.index_producer(producer, provider, show_progress=False)
        assert len(records) == 5
        assert len(store) == 5


class TestFromDictHelpers:
    def test_registry_snapshot_from_dict(self) -> None:
        data = {
            "job_kinds": [{"job_kind": "etl.extract", "title": "ETL Extract"}],
            "implementations": [{"implementation_id": "impl-1", "job_kind": "etl.extract"}],
            "services": [{"service_name": "svc-1"}],
            "schemas": [{"schema_name": "sch-1", "schema_family": "core"}],
        }
        snap = RegistrySnapshot.from_dict(data)
        assert len(snap.job_kinds) == 1
        assert snap.job_kinds[0].job_kind == "etl.extract"
        assert len(snap.implementations) == 1
        assert len(snap.services) == 1
        assert len(snap.schemas) == 1

    def test_job_kind_from_dict(self) -> None:
        jk = JobKindSnapshot.from_dict({"job_kind": "test.kind", "capabilities": ["a", "b"]})
        assert jk.job_kind == "test.kind"
        assert jk.capabilities == ["a", "b"]

    def test_implementation_from_dict(self) -> None:
        impl = ImplementationSnapshot.from_dict({
            "implementation_id": "impl-1",
            "job_kind": "test.kind",
            "dependencies": "single-dep",
        })
        assert impl.implementation_id == "impl-1"
        assert impl.dependencies == ["single-dep"]

    def test_service_from_dict(self) -> None:
        svc = ServiceSnapshot.from_dict({"service_name": "svc-1", "interfaces": ["REST"]})
        assert svc.service_name == "svc-1"
        assert svc.interfaces == ["REST"]

    def test_schema_from_dict(self) -> None:
        sch = SchemaSnapshot.from_dict({
            "schema_name": "sch-1",
            "field_names": ["id", "name"],
            "required_fields": ["id"],
        })
        assert sch.schema_name == "sch-1"
        assert sch.field_names == ["id", "name"]
        assert sch.required_fields == ["id"]
