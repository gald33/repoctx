"""Domain-agnostic retrieval core.

This module provides the shared retrieval engine that operates on
:class:`RetrievableRecord` instances.  It owns:

- record indexing (text → embedding → vector store)
- query execution (query text → scored results)
- metadata filtering
- top-k retrieval

It does **not** assume file paths, chunk ordering, or any repo-specific
semantics.  Domain adapters (e.g. the repo adapter) are responsible for
producing records and interpreting results.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from repoctx.record import (
    MetadataFilter,
    RetrievableRecord,
    RetrievalQuery,
    RetrievalResult,
)

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Embedding interface (protocol so callers can supply their own)
# ---------------------------------------------------------------------------


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Minimal interface an embedding backend must satisfy."""

    @property
    def dimension(self) -> int: ...

    def encode_texts(self, texts: list[str], *, show_progress: bool = True) -> Any:
        """Return an (N, dim) array of L2-normalised vectors."""
        ...

    def encode_query(self, text: str) -> Any:
        """Return a (dim,) L2-normalised query vector."""
        ...


# ---------------------------------------------------------------------------
# Default embedding provider (wraps repoctx.embeddings.EmbeddingModel)
# ---------------------------------------------------------------------------


class DefaultEmbeddingProvider:
    """Adapts :class:`repoctx.embeddings.EmbeddingModel` to :class:`EmbeddingProvider`."""

    def __init__(self) -> None:
        from repoctx.embeddings import EmbeddingModel

        self._model = EmbeddingModel()

    @property
    def dimension(self) -> int:
        return self._model.dimension

    def encode_texts(self, texts: list[str], *, show_progress: bool = True) -> Any:
        return self._model.encode_documents(texts, show_progress=show_progress)

    def encode_query(self, text: str) -> Any:
        return self._model.encode_query(text)


# ---------------------------------------------------------------------------
# RecordStore – persistent record index backed by VectorIndex
# ---------------------------------------------------------------------------


def _record_content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


@dataclass
class RecordStore:
    """Stores :class:`RetrievableRecord` embeddings and supports filtered retrieval.

    Wraps :class:`VectorIndex` internally, translating between
    the generic record model and the low-level vector storage.
    """

    _vec_index: Any = field(default=None, repr=False)
    _record_map: dict[str, RetrievableRecord] = field(default_factory=dict)
    model_name: str = ""
    dimension: int = 0

    def __len__(self) -> int:
        if self._vec_index is None:
            return 0
        return len(self._vec_index)

    # -- indexing -----------------------------------------------------------

    def index_records(
        self,
        records: list[RetrievableRecord],
        provider: EmbeddingProvider,
        *,
        show_progress: bool = True,
    ) -> None:
        """Embed and store *records* in one batch."""
        from repoctx.vector_index import IndexEntry, VectorIndex

        if not records:
            return

        texts = [r.text for r in records]
        vectors = provider.encode_texts(texts, show_progress=show_progress)

        entries = [
            IndexEntry(
                path=r.id,
                kind=r.record_type,
                content_hash=_record_content_hash(r.text),
                namespace=r.namespace,
                record_type=r.record_type,
                metadata=r.metadata,
            )
            for r in records
        ]

        self._vec_index = VectorIndex(
            vectors=vectors,
            entries=entries,
            model_name=self.model_name or getattr(provider, "model_name", ""),
            dimension=provider.dimension,
        )
        self.model_name = self._vec_index.model_name
        self.dimension = self._vec_index.dimension

        for r in records:
            self._record_map[r.id] = r

    def add_record(
        self,
        record: RetrievableRecord,
        provider: EmbeddingProvider,
    ) -> None:
        """Embed and upsert a single record."""
        if self._vec_index is None:
            self.index_records([record], provider, show_progress=False)
            return

        vec = provider.encode_texts([record.text], show_progress=False)[0]
        self._vec_index.update_entry(
            path=record.id,
            kind=record.record_type,
            content_hash=_record_content_hash(record.text),
            vector=vec,
            namespace=record.namespace,
            record_type=record.record_type,
            metadata=record.metadata,
        )
        self._record_map[record.id] = record

    # -- querying ----------------------------------------------------------

    def query(
        self,
        q: RetrievalQuery,
        provider: EmbeddingProvider,
    ) -> list[RetrievalResult]:
        """Execute a retrieval query and return scored results."""
        if self._vec_index is None:
            return []

        query_vec = provider.encode_query(q.text)

        mf: list[tuple[str, list[Any]]] | None = None
        if q.metadata_filters:
            mf = [(f.key, f.values) for f in q.metadata_filters]

        raw = self._vec_index.similarity_scores_by_id(
            query_vec,
            namespace=q.namespace,
            record_types=q.record_types,
            metadata_filters=mf,
        )

        results: list[RetrievalResult] = []
        for entry_id, score, entry in raw:
            if score < q.min_score:
                continue
            results.append(
                RetrievalResult(
                    record_id=entry_id,
                    score=score,
                    record_type=entry.record_type,
                    namespace=entry.namespace,
                    metadata=entry.metadata,
                )
            )
            if len(results) >= q.top_k:
                break

        return results

    # -- persistence -------------------------------------------------------

    def save(self, index_dir: str | Path) -> None:
        if self._vec_index is None:
            raise ValueError("Nothing to save – index is empty")
        self._vec_index.save(index_dir)

    @classmethod
    def load(cls, index_dir: str | Path) -> RecordStore:
        from repoctx.vector_index import VectorIndex

        vec_index = VectorIndex.load(index_dir)
        store = cls(
            _vec_index=vec_index,
            model_name=vec_index.model_name,
            dimension=vec_index.dimension,
        )
        return store

    # -- introspection -----------------------------------------------------

    def get_record(self, record_id: str) -> RetrievableRecord | None:
        return self._record_map.get(record_id)

    @property
    def namespaces(self) -> set[str]:
        if self._vec_index is None:
            return set()
        return {e.namespace for e in self._vec_index.entries}

    @property
    def record_types(self) -> set[str]:
        if self._vec_index is None:
            return set()
        return {e.record_type for e in self._vec_index.entries if e.record_type}
