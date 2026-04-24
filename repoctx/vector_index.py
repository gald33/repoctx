"""Persistent vector index backed by numpy arrays and JSON metadata."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from repoctx.record import MetadataFilter

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)

try:
    import numpy as _np

    HAS_NUMPY = True
except ImportError:
    _np = None  # type: ignore[assignment]
    HAS_NUMPY = False

VECTORS_FILE = "vectors.npy"
METADATA_FILE = "metadata.json"
INDEX_CONFIG_FILE = "index_config.json"


@dataclass(slots=True)
class IndexEntry:
    path: str
    kind: str
    content_hash: str
    namespace: str = "default"
    record_type: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    parent_id: str | None = None
    embedding_ref: str | None = None

    @property
    def id(self) -> str:
        return self.path


@dataclass
class VectorIndex:
    """In-memory vector store with on-disk persistence.

    Vectors are assumed to be L2-normalised so dot product == cosine similarity.
    """

    vectors: Any = None  # np.ndarray (N, dim) float32 | None
    entries: list[IndexEntry] = field(default_factory=list)
    model_name: str = ""
    dimension: int = 0

    def __len__(self) -> int:
        return len(self.entries)

    def similarity_scores(self, query_vector: Any) -> dict[str, float]:
        """Cosine similarity of *query_vector* against every stored vector.

        Returns ``{path: similarity}`` for backward compatibility.
        """
        if not HAS_NUMPY or self.vectors is None or len(self.entries) == 0:
            return {}
        scores = self.vectors @ query_vector
        return {
            entry.path: float(scores[i])
            for i, entry in enumerate(self.entries)
        }

    def similarity_scores_by_id(
        self,
        query_vector: Any,
        *,
        namespace: str | None = None,
        namespaces: list[str] | None = None,
        record_types: list[str] | None = None,
        metadata_filters: list[MetadataFilter] | None = None,
    ) -> list[tuple[str, float, IndexEntry]]:
        """Score every entry against *query_vector* with optional filtering.

        Returns a list of ``(entry.path, score, entry)`` tuples sorted by
        descending score.  Filtering narrows results *before* sorting.
        """
        if not HAS_NUMPY or self.vectors is None or len(self.entries) == 0:
            return []
        scores = self.vectors @ query_vector
        results: list[tuple[str, float, IndexEntry]] = []
        ns_set = set(namespaces) if namespaces else None
        rt_set = set(record_types) if record_types else None
        for i, entry in enumerate(self.entries):
            if namespace is not None and entry.namespace != namespace:
                continue
            if ns_set is not None and entry.namespace not in ns_set:
                continue
            if rt_set is not None and entry.record_type not in rt_set:
                continue
            if metadata_filters:
                if not all(f.matches(entry.metadata) for f in metadata_filters):
                    continue
            results.append((entry.path, float(scores[i]), entry))
        results.sort(key=lambda t: -t[1])
        return results

    # ---- persistence --------------------------------------------------------

    def save(self, index_dir: str | Path) -> None:
        if not HAS_NUMPY:
            raise ImportError("numpy is required to save the vector index")
        d = Path(index_dir)
        d.mkdir(parents=True, exist_ok=True)

        _np.save(d / VECTORS_FILE, self.vectors)

        metadata = []
        for e in self.entries:
            entry_dict: dict[str, Any] = {
                "path": e.path,
                "kind": e.kind,
                "content_hash": e.content_hash,
            }
            if e.namespace != "default":
                entry_dict["namespace"] = e.namespace
            if e.record_type:
                entry_dict["record_type"] = e.record_type
            if e.metadata:
                entry_dict["metadata"] = e.metadata
            if e.parent_id is not None:
                entry_dict["parent_id"] = e.parent_id
            if e.embedding_ref is not None:
                entry_dict["embedding_ref"] = e.embedding_ref
            metadata.append(entry_dict)
        (d / METADATA_FILE).write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        config: dict[str, Any] = {
            "model_name": self.model_name,
            "dimension": self.dimension,
            "file_count": len(self.entries),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        (d / INDEX_CONFIG_FILE).write_text(json.dumps(config, indent=2), encoding="utf-8")
        logger.info("Saved vector index (%d entries) → %s", len(self.entries), d)

    @classmethod
    def load(cls, index_dir: str | Path) -> VectorIndex:
        if not HAS_NUMPY:
            raise ImportError("numpy is required to load the vector index")
        d = Path(index_dir)
        required = (d / VECTORS_FILE, d / METADATA_FILE, d / INDEX_CONFIG_FILE)
        missing = [p.name for p in required if not p.exists()]
        if missing:
            raise FileNotFoundError(
                f"Incomplete vector index in {d} (missing {', '.join(missing)})"
            )

        vectors = _np.load(d / VECTORS_FILE)
        metadata = json.loads((d / METADATA_FILE).read_text(encoding="utf-8"))
        config = json.loads((d / INDEX_CONFIG_FILE).read_text(encoding="utf-8"))

        entries = [
            IndexEntry(
                path=m["path"],
                kind=m["kind"],
                content_hash=m["content_hash"],
                namespace=m.get("namespace", "default"),
                record_type=m.get("record_type", ""),
                metadata=m.get("metadata", {}),
                parent_id=m.get("parent_id"),
                embedding_ref=m.get("embedding_ref"),
            )
            for m in metadata
        ]
        return cls(
            vectors=vectors,
            entries=entries,
            model_name=config.get("model_name", ""),
            dimension=config.get("dimension", 0),
        )

    # ---- single-entry mutation ----------------------------------------------

    def update_entry(
        self,
        path: str,
        kind: str,
        content_hash: str,
        vector: Any,
        *,
        namespace: str = "default",
        record_type: str = "",
        metadata: dict[str, Any] | None = None,
        parent_id: str | None = None,
        embedding_ref: str | None = None,
    ) -> None:
        """Insert or replace the vector for *path*."""
        if not HAS_NUMPY:
            raise ImportError("numpy is required")

        new_entry = IndexEntry(
            path=path,
            kind=kind,
            content_hash=content_hash,
            namespace=namespace,
            record_type=record_type,
            metadata=metadata or {},
            parent_id=parent_id,
            embedding_ref=embedding_ref,
        )
        for i, entry in enumerate(self.entries):
            if entry.path == path:
                self.entries[i] = new_entry
                self.vectors[i] = vector
                return

        self.entries.append(new_entry)
        vec2d = _np.asarray(vector).reshape(1, -1)
        if self.vectors is None or self.vectors.shape[0] == 0:
            self.vectors = vec2d
        else:
            self.vectors = _np.vstack([self.vectors, vec2d])
