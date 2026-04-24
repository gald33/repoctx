"""Generic retrievable record model and query types.

These types form the domain-agnostic foundation of the retrieval framework.
Any domain (repo files, artifact registries, service catalogs) produces
``RetrievableRecord`` values; the retrieval core operates exclusively on them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


MetadataFilterOperator = Literal["in", "equals", "prefix", "contains", "exists"]


@dataclass(slots=True)
class RetrievableRecord:
    """A single unit of retrievable content.

    Records are the atoms of the retrieval framework.  Each record carries
    an opaque *id*, the *text* used for embedding, a *record_type* tag,
    a *namespace* for source isolation, and an arbitrary *metadata* map
    that downstream filters can match against.
    """

    id: str
    text: str
    record_type: str
    namespace: str = "default"
    metadata: dict[str, Any] = field(default_factory=dict)
    parent_id: str | None = None
    embedding_ref: str | None = None

    @property
    def canonical_text(self) -> str:
        """The canonical text that should be embedded for this record."""
        return self.text

    @property
    def source_namespace(self) -> str:
        """Backward-compatible alias used by callers that prefer explicit naming."""
        return self.namespace

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "text": self.text,
            "record_type": self.record_type,
            "namespace": self.namespace,
        }
        if self.metadata:
            payload["metadata"] = self.metadata
        if self.parent_id is not None:
            payload["parent_id"] = self.parent_id
        if self.embedding_ref is not None:
            payload["embedding_ref"] = self.embedding_ref
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RetrievableRecord:
        return cls(
            id=str(payload["id"]),
            text=str(payload["text"]),
            record_type=str(payload["record_type"]),
            namespace=str(payload.get("namespace", "default")),
            metadata=dict(payload.get("metadata", {})),
            parent_id=payload.get("parent_id"),
            embedding_ref=payload.get("embedding_ref"),
        )


@dataclass(slots=True)
class MetadataFilter:
    """Match records whose metadata[key] is in *values*.

    Filters are conjunctive: every filter in a query must match for a
    record to be included.
    """

    key: str
    values: list[Any] = field(default_factory=list)
    operator: MetadataFilterOperator = "in"

    def matches(self, metadata: dict[str, Any]) -> bool:
        val = metadata.get(self.key)
        if self.operator == "exists":
            return self.key in metadata
        if self.operator == "equals":
            return bool(self.values) and val == self.values[0]
        if self.operator == "prefix":
            if val is None:
                return False
            sval = str(val)
            return any(sval.startswith(str(prefix)) for prefix in self.values)
        if self.operator == "contains":
            if val is None:
                return False
            sval = str(val)
            return any(str(item) in sval for item in self.values)
        return val in self.values

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "values": list(self.values),
            "operator": self.operator,
        }


@dataclass(slots=True)
class RetrievalQuery:
    """Parameters for a retrieval request against the generic core."""

    text: str
    top_k: int = 10
    namespace: str | None = None
    namespaces: list[str] | None = None
    record_types: list[str] | None = None
    metadata_filters: list[MetadataFilter] | None = None
    min_score: float = 0.0

    def selected_namespaces(self) -> list[str] | None:
        if self.namespaces:
            return self.namespaces
        if self.namespace is None:
            return None
        return [self.namespace]


@dataclass(slots=True)
class RetrievalResult:
    """A single scored result from retrieval."""

    record_id: str
    score: float
    record_type: str = ""
    namespace: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    parent_id: str | None = None
