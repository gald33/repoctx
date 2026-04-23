"""Generic retrievable record model and query types.

These types form the domain-agnostic foundation of the retrieval framework.
Any domain (repo files, artifact registries, service catalogs) produces
RetrievableRecords; the retrieval core operates exclusively on them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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


@dataclass(slots=True)
class MetadataFilter:
    """Match records whose metadata[key] is in *values*.

    Filters are conjunctive: every filter in a query must match for a
    record to be included.
    """

    key: str
    values: list[Any]

    def matches(self, metadata: dict[str, Any]) -> bool:
        val = metadata.get(self.key)
        return val in self.values


@dataclass(slots=True)
class RetrievalQuery:
    """Parameters for a retrieval request against the generic core."""

    text: str
    top_k: int = 10
    namespace: str | None = None
    record_types: list[str] | None = None
    metadata_filters: list[MetadataFilter] | None = None
    min_score: float = 0.0


@dataclass(slots=True)
class RetrievalResult:
    """A single scored result from retrieval."""

    record_id: str
    score: float
    record_type: str = ""
    namespace: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
