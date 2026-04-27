"""Embedding model wrapper and enriched text builder for semantic retrieval.

Uses sentence-transformers with Qwen3-Embedding-0.6B by default.
All imports are conditional so repoctx works without embedding dependencies.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path, PurePosixPath
from time import perf_counter
from typing import TYPE_CHECKING

from repoctx.chunker import Chunk, ChunkConfig, chunk_record
from repoctx.config import DEFAULT_EMBEDDING_CONFIG, EmbeddingConfig
from repoctx.models import FileRecord
from repoctx.symbols import extract_symbols

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


def build_enriched_chunk_text(record: FileRecord, chunk: Chunk) -> str:
    """Per-chunk enriched text: file/kind/module/symbol/lines header + chunk body.

    Mirrors :func:`build_enriched_text` but adds symbol and line-range hints so
    the embedding captures *which part* of the file we're representing.
    """
    parts = PurePosixPath(record.path).parts
    module = "/".join(parts[:-1]) if len(parts) > 1 else ""

    lines = [f"file: {record.path}", f"kind: {record.kind}"]
    if module:
        lines.append(f"module: {module}")
    if chunk.enclosing_symbol:
        lines.append(f"symbol: {chunk.enclosing_symbol}")
    lines.append(f"lines: {chunk.start_line}-{chunk.end_line}")
    lines.append("")
    lines.append(chunk.text)
    return "\n".join(lines)


def content_hash(content: str) -> str:
    """Stable hash for detecting content changes."""
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()[:16]


def _resolve_device(config: EmbeddingConfig) -> str | None:
    """Pick device for sentence-transformers.

    Priority: REPOCTX_EMBEDDING_DEVICE env var > config.device > auto-detect.
    Returns None to let sentence-transformers auto-detect.
    """
    env = os.environ.get("REPOCTX_EMBEDDING_DEVICE")
    if env:
        return env
    return config.device


def _resolve_batch_size(config: EmbeddingConfig) -> int:
    env = os.environ.get("REPOCTX_EMBEDDING_BATCH_SIZE")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            logger.warning("Invalid REPOCTX_EMBEDDING_BATCH_SIZE=%r; using config", env)
    return config.batch_size


class EmbeddingModel:
    """Thin wrapper around a sentence-transformers model."""

    def __init__(self, config: EmbeddingConfig = DEFAULT_EMBEDDING_CONFIG) -> None:
        if not HAS_EMBEDDINGS:
            raise ImportError(
                "sentence-transformers is required for embeddings. "
                "Install with: pip install 'repoctx-mcp[embeddings]'"
            )
        self.config = config
        device = _resolve_device(config)
        self.batch_size: int = _resolve_batch_size(config)
        logger.info(
            "Loading embedding model %s on %s (batch_size=%d) …",
            config.model_name, device or "auto", self.batch_size,
        )
        kwargs: dict = {"trust_remote_code": True}
        if device:
            kwargs["device"] = device
        self._model = SentenceTransformer(config.model_name, **kwargs)
        self.dimension: int = self._model.get_sentence_embedding_dimension()

    def encode_documents(self, texts: list[str], *, show_progress: bool = True) -> np.ndarray:
        """Encode document texts. Returns (N, dim) float32 array, L2-normalised."""
        if not texts:
            return _np.empty((0, self.dimension), dtype=_np.float32)
        return self._model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=show_progress,
            batch_size=self.batch_size,
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


def _chunks_for_record(
    record: FileRecord, chunk_cfg: ChunkConfig
) -> list[Chunk]:
    """Extract symbols (if applicable) and chunk *record* accordingly."""
    symbols = extract_symbols(record) if record.kind in {"code", "test", "config"} else []
    return chunk_record(record, symbols=symbols, cfg=chunk_cfg)


def _chunk_to_entry(record: FileRecord, chunk: Chunk):
    """Build an IndexEntry for *chunk* of *record*. Imported lazily."""
    from repoctx.vector_index import IndexEntry

    return IndexEntry(
        path=record.path,
        kind=record.kind,
        content_hash=content_hash(chunk.text),
        record_type="chunk",
        metadata={
            "chunk_index": chunk.chunk_index,
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "enclosing_symbol": chunk.enclosing_symbol,
        },
    )


def build_index(
    repo_root: str | Path,
    config: EmbeddingConfig = DEFAULT_EMBEDDING_CONFIG,
    chunk_config: ChunkConfig | None = None,
) -> VectorIndex:
    """Scan a repository, chunk every file, embed each chunk, and return a VectorIndex.

    Each file produces one or more chunks (symbol-aware sliding window for code,
    paragraph-aware for prose). Caller persists with ``index.save(…)``.
    """
    from repoctx.scanner import scan_repository
    from repoctx.vector_index import VectorIndex

    chunk_cfg = chunk_config or ChunkConfig()
    root = Path(repo_root).resolve()
    repo_index = scan_repository(root)
    model = EmbeddingModel(config)

    records: list[FileRecord] = list(repo_index.records.values())
    texts: list[str] = []
    entries_proto: list[tuple[FileRecord, Chunk]] = []
    skipped_empty = 0
    for record in records:
        chunks = _chunks_for_record(record, chunk_cfg)
        if not chunks:
            skipped_empty += 1
            continue
        for chunk in chunks:
            texts.append(build_enriched_chunk_text(record, chunk))
            entries_proto.append((record, chunk))

    logger.info(
        "Embedding %d chunks across %d files (%d empty skipped) …",
        len(texts), len(records) - skipped_empty, skipped_empty,
    )
    started = perf_counter()
    vectors = model.encode_documents(texts)
    elapsed = perf_counter() - started
    logger.info("Embedded %d chunks in %.1f s", len(texts), elapsed)

    entries = [_chunk_to_entry(record, chunk) for record, chunk in entries_proto]
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
    chunk_config: ChunkConfig | None = None,
) -> None:
    """Re-chunk and re-embed a single file, replacing all of its rows on disk."""
    from repoctx.scanner import scan_repository
    from repoctx.vector_index import VectorIndex

    chunk_cfg = chunk_config or ChunkConfig()
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

    chunks = _chunks_for_record(record, chunk_cfg)
    removed = vec_index.delete_by_path(rel_path)
    if not chunks:
        vec_index.save(index_dir)
        logger.info("Removed %d stale chunks for empty %s", removed, rel_path)
        return

    texts = [build_enriched_chunk_text(record, c) for c in chunks]
    vectors = model.encode_documents(texts, show_progress=False)
    entries = [_chunk_to_entry(record, c) for c in chunks]
    vec_index.add_entries(entries, vectors)
    vec_index.save(index_dir)
    logger.info(
        "Updated %s: replaced %d chunks with %d new", rel_path, removed, len(chunks),
    )
