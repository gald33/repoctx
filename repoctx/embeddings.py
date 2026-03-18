"""Embedding model wrapper and enriched text builder for semantic retrieval.

Uses sentence-transformers with Qwen3-Embedding-0.6B by default.
All imports are conditional so repoctx works without embedding dependencies.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path, PurePosixPath
from time import perf_counter
from typing import TYPE_CHECKING

from repoctx.config import DEFAULT_EMBEDDING_CONFIG, EmbeddingConfig
from repoctx.models import FileRecord

if TYPE_CHECKING:
    import numpy as np

    from repoctx.vector_index import VectorIndex

logger = logging.getLogger(__name__)

try:
    from sentence_transformers import SentenceTransformer
    import numpy as _np

    HAS_EMBEDDINGS = True
except ImportError:
    SentenceTransformer = None  # type: ignore[assignment,misc]
    _np = None  # type: ignore[assignment]
    HAS_EMBEDDINGS = False


def build_enriched_text(record: FileRecord, max_content_chars: int = 8000) -> str:
    """Construct metadata-enriched text for embedding a file record.

    Includes path, kind, module hint, and truncated content so the
    embedding captures both structural and semantic information.
    """
    parts = PurePosixPath(record.path).parts
    module = "/".join(parts[:-1]) if len(parts) > 1 else ""

    lines = [f"file: {record.path}", f"kind: {record.kind}"]
    if module:
        lines.append(f"module: {module}")
    lines.append("")

    content = record.content[:max_content_chars] if record.content else ""
    lines.append(content)
    return "\n".join(lines)


def content_hash(content: str) -> str:
    """Stable hash for detecting content changes."""
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()[:16]


class EmbeddingModel:
    """Thin wrapper around a sentence-transformers model."""

    def __init__(self, config: EmbeddingConfig = DEFAULT_EMBEDDING_CONFIG) -> None:
        if not HAS_EMBEDDINGS:
            raise ImportError(
                "sentence-transformers is required for embeddings. "
                "Install with: pip install 'repoctx-mcp[embeddings]'"
            )
        self.config = config
        logger.info("Loading embedding model %s …", config.model_name)
        self._model = SentenceTransformer(config.model_name, trust_remote_code=True)
        self.dimension: int = self._model.get_sentence_embedding_dimension()

    def encode_documents(self, texts: list[str], *, show_progress: bool = True) -> np.ndarray:
        """Encode document texts. Returns (N, dim) float32 array, L2-normalised."""
        if not texts:
            return _np.empty((0, self.dimension), dtype=_np.float32)
        return self._model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=show_progress,
        )

    def encode_query(self, text: str) -> np.ndarray:
        """Encode a single query. Returns (dim,) float32 array, L2-normalised."""
        return self._model.encode(text, normalize_embeddings=True)


class EmbeddingRetriever:
    """Bundles a loaded model and vector index for query-time scoring."""

    def __init__(self, model: EmbeddingModel, index: VectorIndex) -> None:
        self.model = model
        self.index = index

    def query_scores(self, task: str) -> dict[str, float]:
        """Return {path: cosine_similarity} for every indexed file."""
        query_vec = self.model.encode_query(task)
        return self.index.similarity_scores(query_vec)


def try_load_retriever(
    repo_root: str | Path,
    config: EmbeddingConfig = DEFAULT_EMBEDDING_CONFIG,
) -> EmbeddingRetriever | None:
    """Attempt to load model + persisted index. Returns None on any failure."""
    if not HAS_EMBEDDINGS:
        logger.debug("Embedding dependencies not installed – skipping")
        return None
    try:
        from repoctx.vector_index import VectorIndex

        index_dir = Path(repo_root).resolve() / config.index_dir / "embeddings"
        index = VectorIndex.load(index_dir)
        model = EmbeddingModel(config)
        return EmbeddingRetriever(model=model, index=index)
    except Exception as exc:
        logger.info("Embeddings not available: %s", exc)
        return None


def build_index(
    repo_root: str | Path,
    config: EmbeddingConfig = DEFAULT_EMBEDDING_CONFIG,
) -> VectorIndex:
    """Scan a repository, embed every file, and return a VectorIndex.

    Caller is responsible for persisting the index with ``index.save(…)``.
    """
    from repoctx.scanner import scan_repository
    from repoctx.vector_index import VectorIndex

    root = Path(repo_root).resolve()
    repo_index = scan_repository(root)
    model = EmbeddingModel(config)

    records: list[FileRecord] = list(repo_index.records.values())
    texts = [build_enriched_text(r, config.max_content_chars) for r in records]
    hashes = [content_hash(r.content) for r in records]

    logger.info("Embedding %d files …", len(texts))
    started = perf_counter()
    vectors = model.encode_documents(texts)
    elapsed = perf_counter() - started
    logger.info("Embedded %d files in %.1f s", len(texts), elapsed)

    from repoctx.vector_index import IndexEntry

    entries = [
        IndexEntry(path=r.path, kind=r.kind, content_hash=h)
        for r, h in zip(records, hashes)
    ]
    return VectorIndex(
        vectors=vectors,
        entries=entries,
        model_name=config.model_name,
        dimension=model.dimension,
    )


def update_file_in_index(
    file_path: str,
    repo_root: str | Path,
    config: EmbeddingConfig = DEFAULT_EMBEDDING_CONFIG,
) -> None:
    """Re-embed a single file and update the persisted index on disk."""
    from repoctx.scanner import scan_repository
    from repoctx.vector_index import VectorIndex

    root = Path(repo_root).resolve()
    index_dir = root / config.index_dir / "embeddings"
    vec_index = VectorIndex.load(index_dir)
    model = EmbeddingModel(config)

    repo_index = scan_repository(root)
    rel_path = Path(file_path).as_posix()
    if rel_path not in repo_index.records:
        abs_try = (root / file_path).resolve()
        rel_path = abs_try.relative_to(root).as_posix() if abs_try.exists() else rel_path
    record = repo_index.records.get(rel_path)
    if record is None:
        raise FileNotFoundError(f"File not found in repository index: {file_path}")

    text = build_enriched_text(record, config.max_content_chars)
    vector = model.encode_documents([text], show_progress=False)[0]
    chash = content_hash(record.content)
    vec_index.update_entry(rel_path, record.kind, chash, vector)
    vec_index.save(index_dir)
    logger.info("Updated embedding for %s", rel_path)
