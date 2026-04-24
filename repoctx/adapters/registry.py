"""Lucille registry adapter – converts registry artifact snapshots into retrievable records.

This adapter accepts **already-fetched** Lucille registry data (plain dicts or
the lightweight dataclasses defined below) and produces
:class:`RetrievableRecord` instances suitable for the shared ``RecordStore``.

It never performs network/database calls.  Callers are responsible for
providing the artifact snapshot data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from repoctx.record import RetrievableRecord

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LUCILLE_NAMESPACE = "lucille.registry"

VersionMode = Literal["selected", "all"]

# ---------------------------------------------------------------------------
# Lightweight input data types
# ---------------------------------------------------------------------------
# These mirror the shape of Lucille registry exports without importing
# Lucille internals.  Callers may construct them manually, from dicts,
# or via the provided ``from_dict`` helpers.


@dataclass(slots=True)
class JobKindSnapshot:
    job_kind: str
    title: str = ""
    summary: str = ""
    purpose: str = ""
    inputs_summary: str = ""
    outputs_summary: str = ""
    execution_model: str = ""
    integrations: list[str] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    implementations: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    module_backed: bool = False
    resource_locks: list[str] = field(default_factory=list)
    input_schema_id: str = ""
    output_schema_id: str = ""
    version: str = ""
    visibility: str = "public"
    status: str = "active"
    tags: list[str] = field(default_factory=list)
    owner: str = ""
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> JobKindSnapshot:
        return cls(
            job_kind=str(d.get("job_kind", "")),
            title=str(d.get("title", "")),
            summary=str(d.get("summary", "")),
            purpose=str(d.get("purpose", "")),
            inputs_summary=str(d.get("inputs_summary", "")),
            outputs_summary=str(d.get("outputs_summary", "")),
            execution_model=str(d.get("execution_model", "")),
            integrations=_as_str_list(d.get("integrations")),
            services=_as_str_list(d.get("services")),
            implementations=_as_str_list(d.get("implementations")),
            capabilities=_as_str_list(d.get("capabilities")),
            constraints=_as_str_list(d.get("constraints")),
            keywords=_as_str_list(d.get("keywords")),
            module_backed=bool(d.get("module_backed", False)),
            resource_locks=_as_str_list(d.get("resource_locks")),
            input_schema_id=str(d.get("input_schema_id", "")),
            output_schema_id=str(d.get("output_schema_id", "")),
            version=str(d.get("version", "")),
            visibility=str(d.get("visibility", "public")),
            status=str(d.get("status", "active")),
            tags=_as_str_list(d.get("tags")),
            owner=str(d.get("owner", "")),
            created_at=str(d.get("created_at", "")),
            updated_at=str(d.get("updated_at", "")),
        )


@dataclass(slots=True)
class ImplementationSnapshot:
    implementation_id: str
    job_kind: str
    module_name: str = ""
    version: str = ""
    title: str = ""
    summary: str = ""
    runtime: str = ""
    entrypoint: str = ""
    dependencies: list[str] = field(default_factory=list)
    handles: str = ""
    inputs_summary: str = ""
    outputs_summary: str = ""
    failure_modes: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    signature_hash: str = ""
    integrations: list[str] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    input_schema_id: str = ""
    output_schema_id: str = ""
    is_default: bool = False
    visibility: str = "public"
    status: str = "active"
    tags: list[str] = field(default_factory=list)
    owner: str = ""
    created_at: str = ""
    updated_at: str = ""

    @property
    def name(self) -> str:
        return self.module_name or self.implementation_id

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ImplementationSnapshot:
        return cls(
            implementation_id=str(d.get("implementation_id", "")),
            job_kind=str(d.get("job_kind", "")),
            module_name=str(d.get("module_name", "")),
            version=str(d.get("version", "")),
            title=str(d.get("title", "")),
            summary=str(d.get("summary", "")),
            runtime=str(d.get("runtime", "")),
            entrypoint=str(d.get("entrypoint", "")),
            dependencies=_as_str_list(d.get("dependencies")),
            handles=str(d.get("handles", "")),
            inputs_summary=str(d.get("inputs_summary", "")),
            outputs_summary=str(d.get("outputs_summary", "")),
            failure_modes=_as_str_list(d.get("failure_modes")),
            keywords=_as_str_list(d.get("keywords")),
            signature_hash=str(d.get("signature_hash", "")),
            integrations=_as_str_list(d.get("integrations")),
            services=_as_str_list(d.get("services")),
            capabilities=_as_str_list(d.get("capabilities")),
            input_schema_id=str(d.get("input_schema_id", "")),
            output_schema_id=str(d.get("output_schema_id", "")),
            is_default=bool(d.get("is_default", False)),
            visibility=str(d.get("visibility", "public")),
            status=str(d.get("status", "active")),
            tags=_as_str_list(d.get("tags")),
            owner=str(d.get("owner", "")),
            created_at=str(d.get("created_at", "")),
            updated_at=str(d.get("updated_at", "")),
        )


@dataclass(slots=True)
class ServiceSnapshot:
    service_name: str
    title: str = ""
    summary: str = ""
    purpose: str = ""
    interfaces: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    integrations: list[str] = field(default_factory=list)
    job_kinds: list[str] = field(default_factory=list)
    operational_notes: str = ""
    keywords: list[str] = field(default_factory=list)
    auth_mode: str = ""
    deployment_scope: str = ""
    version: str = ""
    visibility: str = "public"
    status: str = "active"
    tags: list[str] = field(default_factory=list)
    owner: str = ""
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ServiceSnapshot:
        return cls(
            service_name=str(d.get("service_name", "")),
            title=str(d.get("title", "")),
            summary=str(d.get("summary", "")),
            purpose=str(d.get("purpose", "")),
            interfaces=_as_str_list(d.get("interfaces")),
            capabilities=_as_str_list(d.get("capabilities")),
            integrations=_as_str_list(d.get("integrations")),
            job_kinds=_as_str_list(d.get("job_kinds")),
            operational_notes=str(d.get("operational_notes", "")),
            keywords=_as_str_list(d.get("keywords")),
            auth_mode=str(d.get("auth_mode", "")),
            deployment_scope=str(d.get("deployment_scope", "")),
            version=str(d.get("version", "")),
            visibility=str(d.get("visibility", "public")),
            status=str(d.get("status", "active")),
            tags=_as_str_list(d.get("tags")),
            owner=str(d.get("owner", "")),
            created_at=str(d.get("created_at", "")),
            updated_at=str(d.get("updated_at", "")),
        )


@dataclass(slots=True)
class SchemaSnapshot:
    schema_name: str
    schema_family: str = ""
    schema_version: str = ""
    title: str = ""
    summary: str = ""
    purpose: str = ""
    entity_type: str = ""
    fields_summary: str = ""
    field_names: list[str] = field(default_factory=list)
    required_fields: list[str] = field(default_factory=list)
    related_services: list[str] = field(default_factory=list)
    related_job_kinds: list[str] = field(default_factory=list)
    compatibility_notes: str = ""
    compatibility_status: str = ""
    keywords: list[str] = field(default_factory=list)
    version: str = ""
    visibility: str = "public"
    status: str = "active"
    tags: list[str] = field(default_factory=list)
    owner: str = ""
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SchemaSnapshot:
        return cls(
            schema_name=str(d.get("schema_name", "")),
            schema_family=str(d.get("schema_family", "")),
            schema_version=str(d.get("schema_version", "")),
            title=str(d.get("title", "")),
            summary=str(d.get("summary", "")),
            purpose=str(d.get("purpose", "")),
            entity_type=str(d.get("entity_type", "")),
            fields_summary=str(d.get("fields_summary", "")),
            field_names=_as_str_list(d.get("field_names")),
            required_fields=_as_str_list(d.get("required_fields")),
            related_services=_as_str_list(d.get("related_services")),
            related_job_kinds=_as_str_list(d.get("related_job_kinds")),
            compatibility_notes=str(d.get("compatibility_notes", "")),
            compatibility_status=str(d.get("compatibility_status", "")),
            keywords=_as_str_list(d.get("keywords")),
            version=str(d.get("version", "")),
            visibility=str(d.get("visibility", "public")),
            status=str(d.get("status", "active")),
            tags=_as_str_list(d.get("tags")),
            owner=str(d.get("owner", "")),
            created_at=str(d.get("created_at", "")),
            updated_at=str(d.get("updated_at", "")),
        )


ArtifactSnapshot = JobKindSnapshot | ImplementationSnapshot | ServiceSnapshot | SchemaSnapshot

# ---------------------------------------------------------------------------
# Registry data container
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RegistrySnapshot:
    """Container for a batch of Lucille registry artifacts to index."""

    job_kinds: list[JobKindSnapshot] = field(default_factory=list)
    implementations: list[ImplementationSnapshot] = field(default_factory=list)
    services: list[ServiceSnapshot] = field(default_factory=list)
    schemas: list[SchemaSnapshot] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RegistrySnapshot:
        return cls(
            job_kinds=[JobKindSnapshot.from_dict(jk) for jk in d.get("job_kinds", [])],
            implementations=[ImplementationSnapshot.from_dict(im) for im in d.get("implementations", [])],
            services=[ServiceSnapshot.from_dict(sv) for sv in d.get("services", [])],
            schemas=[SchemaSnapshot.from_dict(sc) for sc in d.get("schemas", [])],
        )


# ---------------------------------------------------------------------------
# Adapter config
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LucilleRegistryAdapterConfig:
    """Configuration for the Lucille registry adapter."""

    namespace: str = LUCILLE_NAMESPACE
    max_text_chars: int = 6000
    version_mode: VersionMode = "selected"


DEFAULT_REGISTRY_CONFIG = LucilleRegistryAdapterConfig()

# ---------------------------------------------------------------------------
# Stable ID construction
# ---------------------------------------------------------------------------


def job_kind_id(jk: JobKindSnapshot) -> str:
    return f"lucille:job_kind:{jk.job_kind}"


def implementation_id(impl: ImplementationSnapshot) -> str:
    module = impl.module_name or impl.implementation_id
    base = f"lucille:implementation:{impl.job_kind}:{module}"
    if impl.version:
        return f"{base}@{impl.version}"
    return base


def service_id(svc: ServiceSnapshot) -> str:
    return f"lucille:service:{svc.service_name}"


def schema_id(sch: SchemaSnapshot) -> str:
    family = sch.schema_family or "default"
    base = f"lucille:schema:{family}:{sch.schema_name}"
    version = sch.schema_version or sch.version
    if version:
        return f"{base}@{version}"
    return base


# ---------------------------------------------------------------------------
# Canonical text builders
# ---------------------------------------------------------------------------


def _build_job_kind_text(jk: JobKindSnapshot, max_chars: int) -> str:
    lines = [f"job_kind: {jk.job_kind}"]
    if jk.title:
        lines.append(f"title: {jk.title}")
    if jk.summary:
        lines.append(f"summary: {jk.summary}")
    if jk.purpose:
        lines.append(f"purpose: {jk.purpose}")
    if jk.inputs_summary:
        lines.append(f"inputs: {jk.inputs_summary}")
    if jk.outputs_summary:
        lines.append(f"outputs: {jk.outputs_summary}")
    if jk.execution_model:
        lines.append(f"execution_model: {jk.execution_model}")
    if jk.integrations:
        lines.append(f"integrations: {', '.join(jk.integrations)}")
    if jk.services:
        lines.append(f"services: {', '.join(jk.services)}")
    if jk.implementations:
        lines.append(f"implementations: {', '.join(jk.implementations)}")
    if jk.capabilities:
        lines.append(f"capabilities: {', '.join(jk.capabilities)}")
    if jk.constraints:
        lines.append(f"constraints: {', '.join(jk.constraints)}")
    if jk.keywords:
        lines.append(f"keywords: {', '.join(jk.keywords)}")
    return "\n".join(lines)[:max_chars]


def _build_implementation_text(impl: ImplementationSnapshot, max_chars: int) -> str:
    lines = [f"implementation: {impl.name}"]
    if impl.job_kind:
        lines.append(f"job_kind: {impl.job_kind}")
    if impl.module_name:
        lines.append(f"module: {impl.module_name}")
    if impl.version:
        lines.append(f"version: {impl.version}")
    if impl.title:
        lines.append(f"title: {impl.title}")
    if impl.summary:
        lines.append(f"summary: {impl.summary}")
    if impl.runtime:
        lines.append(f"runtime: {impl.runtime}")
    if impl.entrypoint:
        lines.append(f"entrypoint: {impl.entrypoint}")
    if impl.dependencies:
        lines.append(f"dependencies: {', '.join(impl.dependencies)}")
    if impl.handles:
        lines.append(f"handles: {impl.handles}")
    if impl.inputs_summary:
        lines.append(f"inputs: {impl.inputs_summary}")
    if impl.outputs_summary:
        lines.append(f"outputs: {impl.outputs_summary}")
    if impl.failure_modes:
        lines.append(f"failure_modes: {', '.join(impl.failure_modes)}")
    if impl.capabilities:
        lines.append(f"capabilities: {', '.join(impl.capabilities)}")
    if impl.keywords:
        lines.append(f"keywords: {', '.join(impl.keywords)}")
    return "\n".join(lines)[:max_chars]


def _build_service_text(svc: ServiceSnapshot, max_chars: int) -> str:
    lines = [f"service: {svc.service_name}"]
    if svc.title:
        lines.append(f"title: {svc.title}")
    if svc.summary:
        lines.append(f"summary: {svc.summary}")
    if svc.purpose:
        lines.append(f"purpose: {svc.purpose}")
    if svc.interfaces:
        lines.append(f"interfaces: {', '.join(svc.interfaces)}")
    if svc.capabilities:
        lines.append(f"capabilities: {', '.join(svc.capabilities)}")
    if svc.integrations:
        lines.append(f"integrations: {', '.join(svc.integrations)}")
    if svc.job_kinds:
        lines.append(f"job_kinds: {', '.join(svc.job_kinds)}")
    if svc.operational_notes:
        lines.append(f"operational_notes: {svc.operational_notes}")
    if svc.keywords:
        lines.append(f"keywords: {', '.join(svc.keywords)}")
    return "\n".join(lines)[:max_chars]


def _build_schema_text(sch: SchemaSnapshot, max_chars: int) -> str:
    lines = [f"schema: {sch.schema_name}"]
    if sch.schema_family:
        lines.append(f"family: {sch.schema_family}")
    version = sch.schema_version or sch.version
    if version:
        lines.append(f"version: {version}")
    if sch.title:
        lines.append(f"title: {sch.title}")
    if sch.summary:
        lines.append(f"summary: {sch.summary}")
    if sch.purpose:
        lines.append(f"purpose: {sch.purpose}")
    if sch.entity_type:
        lines.append(f"entity_type: {sch.entity_type}")
    if sch.fields_summary:
        lines.append(f"fields: {sch.fields_summary}")
    if sch.required_fields:
        lines.append(f"required_fields: {', '.join(sch.required_fields)}")
    if sch.related_services:
        lines.append(f"related_services: {', '.join(sch.related_services)}")
    if sch.related_job_kinds:
        lines.append(f"related_job_kinds: {', '.join(sch.related_job_kinds)}")
    if sch.compatibility_notes:
        lines.append(f"compatibility: {sch.compatibility_notes}")
    if sch.keywords:
        lines.append(f"keywords: {', '.join(sch.keywords)}")
    return "\n".join(lines)[:max_chars]


# ---------------------------------------------------------------------------
# Metadata builders
# ---------------------------------------------------------------------------


def _common_metadata(
    *,
    artifact_type: str,
    name: str,
    title: str,
    version: str,
    visibility: str,
    status: str,
    tags: list[str],
    keywords: list[str],
    owner: str,
    created_at: str,
    updated_at: str,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "source_system": "lucille",
        "adapter": "registry",
        "artifact_type": artifact_type,
        "name": name,
        "title": title,
        "version": version,
        "version_present": bool(version),
        "visibility": visibility,
        "status": status,
        "tags": _normalize_list(tags),
        "keywords": _normalize_list(keywords),
        "owner": owner,
        "created_at": created_at,
        "updated_at": updated_at,
    }
    return meta


def _job_kind_metadata(jk: JobKindSnapshot) -> dict[str, Any]:
    meta = _common_metadata(
        artifact_type="job_kind",
        name=jk.job_kind,
        title=jk.title,
        version=jk.version,
        visibility=jk.visibility,
        status=jk.status,
        tags=jk.tags,
        keywords=jk.keywords,
        owner=jk.owner,
        created_at=jk.created_at,
        updated_at=jk.updated_at,
    )
    meta.update({
        "job_kind": jk.job_kind,
        "execution_model": jk.execution_model,
        "module_backed": jk.module_backed,
        "integration_ids": _normalize_list(jk.integrations),
        "service_ids": _normalize_list(jk.services),
        "implementation_ids": _normalize_list(jk.implementations),
        "capabilities": _normalize_list(jk.capabilities),
        "resource_locks": _normalize_list(jk.resource_locks),
        "input_schema_id": jk.input_schema_id,
        "output_schema_id": jk.output_schema_id,
    })
    return meta


def _implementation_metadata(impl: ImplementationSnapshot) -> dict[str, Any]:
    meta = _common_metadata(
        artifact_type="implementation",
        name=impl.name,
        title=impl.title,
        version=impl.version,
        visibility=impl.visibility,
        status=impl.status,
        tags=impl.tags,
        keywords=impl.keywords,
        owner=impl.owner,
        created_at=impl.created_at,
        updated_at=impl.updated_at,
    )
    meta.update({
        "implementation_id": impl.implementation_id,
        "job_kind": impl.job_kind,
        "module_name": impl.module_name,
        "runtime": impl.runtime,
        "entrypoint": impl.entrypoint,
        "signature_hash": impl.signature_hash,
        "integration_ids": _normalize_list(impl.integrations),
        "service_ids": _normalize_list(impl.services),
        "dependency_ids": _normalize_list(impl.dependencies),
        "capabilities": _normalize_list(impl.capabilities),
        "input_schema_id": impl.input_schema_id,
        "output_schema_id": impl.output_schema_id,
        "is_default": impl.is_default,
    })
    return meta


def _service_metadata(svc: ServiceSnapshot) -> dict[str, Any]:
    meta = _common_metadata(
        artifact_type="service",
        name=svc.service_name,
        title=svc.title,
        version=svc.version,
        visibility=svc.visibility,
        status=svc.status,
        tags=svc.tags,
        keywords=svc.keywords,
        owner=svc.owner,
        created_at=svc.created_at,
        updated_at=svc.updated_at,
    )
    meta.update({
        "service_name": svc.service_name,
        "interface_types": _normalize_list(svc.interfaces),
        "integration_ids": _normalize_list(svc.integrations),
        "job_kind_ids": _normalize_list(svc.job_kinds),
        "capabilities": _normalize_list(svc.capabilities),
        "auth_mode": svc.auth_mode,
        "deployment_scope": svc.deployment_scope,
    })
    return meta


def _schema_metadata(sch: SchemaSnapshot) -> dict[str, Any]:
    version = sch.schema_version or sch.version
    meta = _common_metadata(
        artifact_type="schema",
        name=sch.schema_name,
        title=sch.title,
        version=version,
        visibility=sch.visibility,
        status=sch.status,
        tags=sch.tags,
        keywords=sch.keywords,
        owner=sch.owner,
        created_at=sch.created_at,
        updated_at=sch.updated_at,
    )
    meta.update({
        "schema_name": sch.schema_name,
        "schema_family": sch.schema_family,
        "schema_version": version,
        "entity_type": sch.entity_type,
        "field_names": _normalize_list(sch.field_names),
        "required_fields": _normalize_list(sch.required_fields),
        "related_job_kind_ids": _normalize_list(sch.related_job_kinds),
        "related_service_ids": _normalize_list(sch.related_services),
        "compatibility_status": sch.compatibility_status,
    })
    return meta


# ---------------------------------------------------------------------------
# Per-artifact record conversion
# ---------------------------------------------------------------------------


def job_kind_to_record(
    jk: JobKindSnapshot,
    config: LucilleRegistryAdapterConfig = DEFAULT_REGISTRY_CONFIG,
) -> RetrievableRecord:
    return RetrievableRecord(
        id=job_kind_id(jk),
        text=_build_job_kind_text(jk, config.max_text_chars),
        record_type="job_kind_summary",
        namespace=config.namespace,
        metadata=_job_kind_metadata(jk),
    )


def implementation_to_record(
    impl: ImplementationSnapshot,
    config: LucilleRegistryAdapterConfig = DEFAULT_REGISTRY_CONFIG,
) -> RetrievableRecord:
    parent = f"lucille:job_kind:{impl.job_kind}" if impl.job_kind else None
    return RetrievableRecord(
        id=implementation_id(impl),
        text=_build_implementation_text(impl, config.max_text_chars),
        record_type="implementation_summary",
        namespace=config.namespace,
        metadata=_implementation_metadata(impl),
        parent_id=parent,
    )


def service_to_record(
    svc: ServiceSnapshot,
    config: LucilleRegistryAdapterConfig = DEFAULT_REGISTRY_CONFIG,
) -> RetrievableRecord:
    return RetrievableRecord(
        id=service_id(svc),
        text=_build_service_text(svc, config.max_text_chars),
        record_type="service_summary",
        namespace=config.namespace,
        metadata=_service_metadata(svc),
    )


def schema_to_record(
    sch: SchemaSnapshot,
    config: LucilleRegistryAdapterConfig = DEFAULT_REGISTRY_CONFIG,
) -> RetrievableRecord:
    return RetrievableRecord(
        id=schema_id(sch),
        text=_build_schema_text(sch, config.max_text_chars),
        record_type="schema_summary",
        namespace=config.namespace,
        metadata=_schema_metadata(sch),
    )


# ---------------------------------------------------------------------------
# Record producer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class LucilleRegistryRecordProducer:
    """Produces :class:`RetrievableRecord` instances from a Lucille registry snapshot.

    Accepts a :class:`RegistrySnapshot` (or individual artifact lists) and
    converts them into the generic record model.  Implements the
    ``RecordProducer`` protocol so it can be passed directly to
    ``RecordStore.index_producer``.
    """

    snapshot: RegistrySnapshot
    config: LucilleRegistryAdapterConfig = DEFAULT_REGISTRY_CONFIG

    def build_records(self) -> list[RetrievableRecord]:
        records: list[RetrievableRecord] = []
        records.extend(self.build_records_for_job_kinds(self.snapshot.job_kinds))
        records.extend(self.build_records_for_implementations(self.snapshot.implementations))
        records.extend(self.build_records_for_services(self.snapshot.services))
        records.extend(self.build_records_for_schemas(self.snapshot.schemas))
        return records

    def build_records_for_job_kinds(
        self,
        job_kinds: list[JobKindSnapshot],
    ) -> list[RetrievableRecord]:
        return [job_kind_to_record(jk, self.config) for jk in job_kinds]

    def build_records_for_implementations(
        self,
        implementations: list[ImplementationSnapshot],
    ) -> list[RetrievableRecord]:
        return [implementation_to_record(impl, self.config) for impl in implementations]

    def build_records_for_services(
        self,
        services: list[ServiceSnapshot],
    ) -> list[RetrievableRecord]:
        return [service_to_record(svc, self.config) for svc in services]

    def build_records_for_schemas(
        self,
        schemas: list[SchemaSnapshot],
    ) -> list[RetrievableRecord]:
        return [schema_to_record(sch, self.config) for sch in schemas]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _as_str_list(val: Any) -> list[str]:
    if val is None:
        return []
    if isinstance(val, str):
        return [val] if val else []
    return [str(item) for item in val]


def _normalize_list(items: list[str]) -> str:
    """Join list items with ``|`` for compact, filterable metadata storage."""
    return "|".join(items) if items else ""
