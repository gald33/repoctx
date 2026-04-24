"""Domain-agnostic retrieval core.

The core owns embedding, indexing, persistence, and filtered query execution.
Adapters are responsible for producing ``RetrievableRecord`` instances from a
domain such as a repository checkout or an artifact registry export.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from repoctx.record import RetrievableRecord, RetrievalQuery, RetrievalResult

logger = logging.getLogger(__name__)

RECORDS_FILE = "records.json"


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Minimal interface an embedding backend must satisfy."""

    @property
    def dimension(self) -> int: ...

    def encode_texts(self, texts: list[str], *, show_progress: bool = True) -> Any:
        """Return an ``(N, dim)`` array of L2-normalized vectors."""

    def encode_query(self, text: str) -> Any:
        """Return a ``(dim,)`` L2-normalized query vector."""


@runtime_checkable
class RecordProducer(Protocol):
    """Adapter protocol for anything that can produce retrievable records."""

    def build_records(self) -> list[RetrievableRecord]:
        """Return the records the retrieval core should index."""


class DefaultEmbeddingProvider:
    """Adapts ``repoctx.embeddings.EmbeddingModel`` to the generic provider."""

    def __init__(self) -> None:
        from repoctx.embeddings import EmbeddingModel

        self._model = EmbeddingModel()

    @property
    def dimension(self) -> int:
        return self._model.dimension

    @property
    def model_name(self) -> str:
        return self._model.config.model_name

    def encode_texts(self, texts: list[str], *, show_progress: bool = True) -> Any:
        return self._model.encode_documents(texts, show_progress=show_progress)

    def encode_query(self, text: str) -> Any:
        return self._model.encode_query(text)


def _record_content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


@dataclass
class RecordStore:
    """Stores record embeddings and supports metadata-aware retrieval."""

    _vec_index: Any = field(default=None, repr=False)
    _record_map: dict[str, RetrievableRecord] = field(default_factory=dict)
    model_name: str = ""
    dimension: int = 0

    def __len__(self) -> int:
        if self._vec_index is None:
            return 0
        return len(self._vec_index)

    def index_records(
        self,
        records: list[RetrievableRecord],
        provider: EmbeddingProvider,
        *,
        show_progress: bool = True,
    ) -> None:
        """Embed and replace the index contents with ``records``."""
        from repoctx.vector_index import IndexEntry, VectorIndex

        if not records:
            return

        vectors = provider.encode_texts(
            [record.canonical_text for record in records],
            show_progress=show_progress,
        )
        entries = [
            IndexEntry(
                path=record.id,
                kind=record.record_type,
                content_hash=record.embedding_ref or _record_content_hash(record.canonical_text),
                namespace=record.namespace,
                record_type=record.record_type,
                metadata=dict(record.metadata),
                parent_id=record.parent_id,
                embedding_ref=record.embedding_ref,
            )
            for record in records
        ]
        self._vec_index = VectorIndex(
            vectors=vectors,
            entries=entries,
            model_name=self.model_name or getattr(provider, "model_name", ""),
            dimension=provider.dimension,
        )
        self.model_name = self._vec_index.model_name
        self.dimension = self._vec_index.dimension
        self._record_map = {record.id: record for record in records}

    def index_producer(
        self,
        producer: RecordProducer,
        provider: EmbeddingProvider,
        *,
        show_progress: bool = True,
    ) -> list[RetrievableRecord]:
        """Build records through an adapter and index them."""
        records = producer.build_records()
        self.index_records(records, provider, show_progress=show_progress)
        return records

    def add_record(
        self,
        record: RetrievableRecord,
        provider: EmbeddingProvider,
    ) -> None:
        """Embed and upsert a single record."""
        if self._vec_index is None:
            self.index_records([record], provider, show_progress=False)
            return

        vector = provider.encode_texts([record.canonical_text], show_progress=False)[0]
        self._vec_index.update_entry(
            path=record.id,
            kind=record.record_type,
            content_hash=record.embedding_ref or _record_content_hash(record.canonical_text),
            vector=vector,
            namespace=record.namespace,
            record_type=record.record_type,
            metadata=dict(record.metadata),
            parent_id=record.parent_id,
            embedding_ref=record.embedding_ref,
        )
        self._record_map[record.id] = record

    def query(
        self,
        q: RetrievalQuery,
        provider: EmbeddingProvider,
    ) -> list[RetrievalResult]:
        """Execute a retrieval query and return scored results."""
        if self._vec_index is None:
            return []

        raw_results = self._vec_index.similarity_scores_by_id(
            provider.encode_query(q.text),
            namespace=q.namespace,
            namespaces=q.selected_namespaces(),
            record_types=q.record_types,
            metadata_filters=q.metadata_filters,
        )

        results: list[RetrievalResult] = []
        for record_id, score, entry in raw_results:
            if score < q.min_score:
                continue
            record = self._record_map.get(record_id)
            results.append(
                RetrievalResult(
                    record_id=record_id,
                    score=score,
                    record_type=entry.record_type,
                    namespace=entry.namespace,
                    metadata=dict(record.metadata if record is not None else entry.metadata),
                    parent_id=record.parent_id if record is not None else entry.parent_id,
                )
            )
            if len(results) >= q.top_k:
                break
        return results

    def similarity_scores(
        self,
        text: str,
        provider: EmbeddingProvider,
        *,
        namespace: str | None = None,
        namespaces: list[str] | None = None,
        record_types: list[str] | None = None,
    ) -> dict[str, float]:
        """Compatibility helper for callers that still want ``id -> score``."""
        query = RetrievalQuery(
            text=text,
            top_k=max(len(self), 1),
            namespace=namespace,
            namespaces=namespaces,
            record_types=record_types,
        )
        return {result.record_id: result.score for result in self.query(query, provider)}

    def save(self, index_dir: str | Path) -> None:
        if self._vec_index is None:
            raise ValueError("Nothing to save – index is empty")
        index_path = Path(index_dir)
        index_path.mkdir(parents=True, exist_ok=True)
        self._vec_index.save(index_path)
        records_payload = [record.to_dict() for record in self._record_map.values()]
        (index_path / RECORDS_FILE).write_text(
            json.dumps(records_payload, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, index_dir: str | Path) -> RecordStore:
        from repoctx.vector_index import VectorIndex

        index_path = Path(index_dir)
        vec_index = VectorIndex.load(index_path)
        record_map: dict[str, RetrievableRecord] = {}
        records_path = index_path / RECORDS_FILE
        if records_path.exists():
            payload = json.loads(records_path.read_text(encoding="utf-8"))
            record_map = {
                record.id: record
                for record in (RetrievableRecord.from_dict(item) for item in payload)
            }
        else:
            for entry in vec_index.entries:
                record_map[entry.id] = RetrievableRecord(
                    id=entry.id,
                    text="",
                    record_type=entry.record_type or entry.kind,
                    namespace=entry.namespace,
                    metadata=dict(entry.metadata),
                    parent_id=entry.parent_id,
                    embedding_ref=entry.embedding_ref,
                )
        return cls(
            _vec_index=vec_index,
            _record_map=record_map,
            model_name=vec_index.model_name,
            dimension=vec_index.dimension,
        )

    def get_record(self, record_id: str) -> RetrievableRecord | None:
        return self._record_map.get(record_id)

    @property
    def namespaces(self) -> set[str]:
        if self._vec_index is None:
            return set()
        return {entry.namespace for entry in self._vec_index.entries}

    @property
    def record_types(self) -> set[str]:
        if self._vec_index is None:
            return set()
        return {entry.record_type for entry in self._vec_index.entries if entry.record_type}


class RetrievalEngine:
    """Thin orchestration wrapper around a ``RecordStore`` and provider."""

    def __init__(self, store: RecordStore, provider: EmbeddingProvider) -> None:
        self.store = store
        self.provider = provider

    def query(self, query: RetrievalQuery) -> list[RetrievalResult]:
        return self.store.query(query, self.provider)

    def similarity_scores(
        self,
        text: str,
        *,
        namespace: str | None = None,
        namespaces: list[str] | None = None,
        record_types: list[str] | None = None,
    ) -> dict[str, float]:
        return self.store.similarity_scores(
            text,
            self.provider,
            namespace=namespace,
            namespaces=namespaces,
            record_types=record_types,
        )
