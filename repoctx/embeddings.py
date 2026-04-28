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


def _resolve_max_seq_length(config: EmbeddingConfig) -> int:
    env = os.environ.get("REPOCTX_EMBEDDING_MAX_SEQ_LENGTH")
    if env:
        try:
            return max(8, int(env))
        except ValueError:
            logger.warning("Invalid REPOCTX_EMBEDDING_MAX_SEQ_LENGTH=%r; using config", env)
    return config.max_seq_length


def _resolve_dtype(config: EmbeddingConfig, device: str) -> str:
    """Return 'fp16' or 'fp32'. 'auto' picks fp16 on accelerators, fp32 on CPU."""
    env = os.environ.get("REPOCTX_EMBEDDING_DTYPE", config.dtype).lower()
    if env in {"fp16", "float16", "half"}:
        return "fp16"
    if env in {"fp32", "float32", "full"}:
        return "fp32"
    # auto / anything else
    return "fp16" if device.startswith(("mps", "cuda")) else "fp32"


# Empirically safe upper bound for MPS Metal buffer allocations on a 16 GB
# Apple silicon machine encoding ~256-token chunks at fp16. Larger batches
# risk tripping the unrecoverable `Failed to allocate private MTLBuffer`
# assertion (a C++ abort, not a Python exception — uncatchable). Auto-clamped
# when the resolved device is MPS.
_MPS_MAX_BATCH = 8

# Number of accelerator mini-batches to run before forcibly evicting the
# device cache. On MPS/CUDA, sentence-transformers' inner batching doesn't
# free heap between mini-batches, so fragmentation accumulates over a long
# encode. We chunk inputs into super-batches of (batch_size × this) and call
# torch.{mps,cuda}.empty_cache() between super-batches to bound peak heap.
_SUPER_BATCH_MULTIPLIER = 8


class EmbeddingModel:
    """Thin wrapper around a sentence-transformers model."""

    def __init__(self, config: EmbeddingConfig = DEFAULT_EMBEDDING_CONFIG) -> None:
        if not HAS_EMBEDDINGS:
            raise ImportError(
                "sentence-transformers is required for embeddings. "
                "Install with: pip install 'repoctx-mcp[embeddings]'"
            )
        self.config = config
        requested_device = _resolve_device(config)
        self._device: str = self._load_model(config, requested_device)
        self.batch_size: int = self._resolve_effective_batch_size(config, self._device)
        self._apply_dtype_and_seq_length()
        self.dimension: int = self._model.get_sentence_embedding_dimension()
        logger.info(
            "Embedding model %s loaded on %s (batch_size=%d, max_seq_length=%d, dtype=%s)",
            config.model_name, self._device, self.batch_size,
            self._model.max_seq_length, self._dtype,
        )

    def _load_model(self, config: EmbeddingConfig, device: str | None) -> str:
        kwargs: dict = {"trust_remote_code": True}
        if device:
            kwargs["device"] = device
        self._model = SentenceTransformer(config.model_name, **kwargs)
        # sentence-transformers exposes the resolved device on `.device`;
        # str() it for stable comparison ("cpu", "mps", "cuda:0", ...).
        resolved = str(getattr(self._model, "device", device or "cpu"))
        return resolved

    def _apply_dtype_and_seq_length(self) -> None:
        """Cast to fp16 (if applicable) and shorten max_seq_length.

        These two tunables are the dominant memory savers — fp16 halves
        weight & activation footprint, and seq_length cuts attention's
        quadratic peak roughly 4× per halving. Together: ~6-8× reduction
        on MPS for typical chunk sizes.
        """
        self._dtype = _resolve_dtype(self.config, self._device)
        if self._dtype == "fp16":
            try:
                self._model = self._model.half()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("fp16 cast failed (%s); staying on fp32", exc)
                self._dtype = "fp32"
        seq = _resolve_max_seq_length(self.config)
        # Don't exceed the model's tokenizer limit.
        try:
            limit = getattr(self._model.tokenizer, "model_max_length", seq)
            if isinstance(limit, int) and limit > 0:
                seq = min(seq, limit)
        except Exception:
            pass
        self._model.max_seq_length = seq

    @staticmethod
    def _resolve_effective_batch_size(config: EmbeddingConfig, device: str) -> int:
        configured = _resolve_batch_size(config)
        if device.startswith("mps") and configured > _MPS_MAX_BATCH:
            logger.warning(
                "Clamping batch_size %d → %d on MPS to reduce Metal allocator pressure. "
                "Set REPOCTX_EMBEDDING_BATCH_SIZE to override.",
                configured, _MPS_MAX_BATCH,
            )
            return _MPS_MAX_BATCH
        return configured

    def _move_to_cpu(self) -> None:
        """Recreate the model on CPU after a device-specific failure."""
        self._model = self._model.to("cpu")
        self._device = "cpu"
        # CPU is faster in fp32 in PyTorch, and the memory pressure that
        # forced fp16 doesn't apply here. Cast back if we were on fp16.
        if self._dtype == "fp16":
            try:
                self._model = self._model.float()
                self._dtype = "fp32"
            except Exception:  # pragma: no cover - defensive
                pass
        # Recompute effective batch size now that we're off the constrained device.
        self.batch_size = _resolve_batch_size(self.config)

    def _empty_device_cache(self) -> None:
        """Evict accelerator memory cache between super-batches.

        No-op on CPU. Wrapped in try/except since some torch builds expose
        `torch.mps.empty_cache` and others don't.
        """
        if not self._device.startswith(("mps", "cuda")):
            return
        try:
            import torch  # type: ignore[import-not-found]

            if self._device.startswith("mps") and hasattr(torch, "mps"):
                empty = getattr(torch.mps, "empty_cache", None)
                if empty:
                    empty()
            elif self._device.startswith("cuda"):
                torch.cuda.empty_cache()
        except Exception:  # pragma: no cover - defensive
            pass

    def _encode_call(self, texts, show_progress: bool):
        """Single encode call with the configured knobs. Used by both paths."""
        return self._model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=show_progress,
            batch_size=self.batch_size,
        )

    def encode_documents(self, texts: list[str], *, show_progress: bool = True) -> np.ndarray:
        """Encode document texts. Returns (N, dim) float32 array, L2-normalised.

        On accelerators, the input is split into super-batches and the device
        cache is evicted between them to bound peak heap fragmentation. On
        CPU, runs as one call.

        On a catchable device error (most commonly MPS/CUDA OOM raised as
        ``RuntimeError``), automatically falls back to CPU and retries.
        """
        if not texts:
            return _np.empty((0, self.dimension), dtype=_np.float32)

        on_accelerator = self._device.startswith(("mps", "cuda"))
        super_batch = self.batch_size * _SUPER_BATCH_MULTIPLIER

        try:
            if not on_accelerator or len(texts) <= super_batch:
                return self._encode_call(texts, show_progress)
            # Super-batched path: encode in groups, evict cache between.
            parts: list[np.ndarray] = []
            n = len(texts)
            for i in range(0, n, super_batch):
                sub = texts[i : i + super_batch]
                # Show progress only on the first sub-call so the bar doesn't
                # repeat; users still see overall progress via logging below.
                parts.append(self._encode_call(sub, show_progress and i == 0))
                self._empty_device_cache()
                if show_progress:
                    logger.info(
                        "Encoded %d / %d (%s super-batches)",
                        min(i + super_batch, n), n, self._device,
                    )
            return _np.vstack(parts)
        except RuntimeError as exc:
            if self._device == "cpu":
                raise
            logger.warning(
                "encode_documents failed on %s (%s); falling back to CPU and retrying.",
                self._device, exc,
            )
            self._move_to_cpu()
            return self._encode_call(texts, show_progress)

    def encode_query(self, text: str) -> np.ndarray:
        """Encode a single query. Returns (dim,) float32 array, L2-normalised."""
        try:
            return self._model.encode(text, normalize_embeddings=True)
        except RuntimeError as exc:
            if self._device == "cpu":
                raise
            logger.warning(
                "encode_query failed on %s (%s); falling back to CPU.",
                self._device, exc,
            )
            self._move_to_cpu()
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


def _chunk_config_to_dict(cfg: ChunkConfig) -> dict[str, int]:
    """Serializable view of a ChunkConfig (for index_config.json)."""
    return {
        "target_tokens": cfg.target_tokens,
        "max_tokens": cfg.max_tokens,
        "overlap_tokens": cfg.overlap_tokens,
        "min_tokens": cfg.min_tokens,
    }


def _load_compatible_existing_index(
    root: Path,
    config: EmbeddingConfig,
    chunk_cfg: ChunkConfig,
) -> VectorIndex | None:
    """Load the on-disk index iff it's compatible for an incremental rebuild.

    Returns None (and logs a warning) on missing index, schema mismatch,
    different model_name, missing or different chunk_config — all cases where
    splicing previously-embedded vectors would be unsafe.
    """
    from repoctx.vector_index import IndexSchemaMismatch, VectorIndex

    index_dir = root / config.index_dir / "embeddings"
    try:
        existing = VectorIndex.load(index_dir)
    except FileNotFoundError as exc:
        logger.warning("Incremental fallback: no existing index (%s)", exc)
        return None
    except IndexSchemaMismatch as exc:
        logger.warning("Incremental fallback: schema mismatch (%s)", exc)
        return None

    if existing.model_name != config.model_name:
        logger.warning(
            "Incremental fallback: model_name changed (%r → %r); doing full rebuild",
            existing.model_name, config.model_name,
        )
        return None
    desired = _chunk_config_to_dict(chunk_cfg)
    if not existing.chunk_config:
        logger.warning(
            "Incremental fallback: existing index has no recorded chunk_config; "
            "doing full rebuild"
        )
        return None
    if existing.chunk_config != desired:
        logger.warning(
            "Incremental fallback: chunk_config changed (%s → %s); doing full rebuild",
            existing.chunk_config, desired,
        )
        return None
    return existing


def build_index(
    repo_root: str | Path,
    config: EmbeddingConfig = DEFAULT_EMBEDDING_CONFIG,
    chunk_config: ChunkConfig | None = None,
    incremental: bool = False,
) -> VectorIndex:
    """Scan a repository, chunk every file, embed each chunk, and return a VectorIndex.

    Each file produces one or more chunks (symbol-aware sliding window for code,
    paragraph-aware for prose). Caller persists with ``index.save(…)``.

    With ``incremental=True``, an existing on-disk index is loaded and only
    chunks whose ``content_hash`` differs (or whose ``(path, chunk_index)``
    didn't exist before) are re-embedded. Chunks present in the old index but
    no longer produced by the current scan are dropped. If the old index is
    missing, schema-incompatible, or built with a different model/chunker, we
    log a warning and fall back to a full rebuild.
    """
    from repoctx.scanner import scan_repository
    from repoctx.vector_index import VectorIndex

    chunk_cfg = chunk_config or ChunkConfig()
    root = Path(repo_root).resolve()

    existing: VectorIndex | None = None
    if incremental:
        existing = _load_compatible_existing_index(root, config, chunk_cfg)

    repo_index = scan_repository(root)
    records: list[FileRecord] = list(repo_index.records.values())
    entries_proto: list[tuple[FileRecord, Chunk]] = []
    skipped_empty = 0
    for record in records:
        chunks = _chunks_for_record(record, chunk_cfg)
        if not chunks:
            skipped_empty += 1
            continue
        for chunk in chunks:
            entries_proto.append((record, chunk))

    if existing is None:
        return _full_build(
            entries_proto, config, chunk_cfg, len(records), skipped_empty,
        )
    return _incremental_build(
        existing, entries_proto, config, chunk_cfg,
    )


def _full_build(
    entries_proto: list[tuple[FileRecord, Chunk]],
    config: EmbeddingConfig,
    chunk_cfg: ChunkConfig,
    total_records: int,
    skipped_empty: int,
) -> VectorIndex:
    from repoctx.vector_index import VectorIndex

    model = EmbeddingModel(config)
    texts = [build_enriched_chunk_text(record, chunk) for record, chunk in entries_proto]

    logger.info(
        "Embedding %d chunks across %d files (%d empty skipped) …",
        len(texts), total_records - skipped_empty, skipped_empty,
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
        chunk_config=_chunk_config_to_dict(chunk_cfg),
    )


def _incremental_build(
    existing: VectorIndex,
    entries_proto: list[tuple[FileRecord, Chunk]],
    config: EmbeddingConfig,
    chunk_cfg: ChunkConfig,
) -> VectorIndex:
    """Re-embed only changed/new chunks; reuse vectors for unchanged ones."""
    from repoctx.vector_index import VectorIndex

    # Build (path, chunk_index) → (row_index, content_hash) lookup over old.
    by_key: dict[tuple[str, int], tuple[int, str]] = {}
    for i, e in enumerate(existing.entries):
        ci = e.metadata.get("chunk_index")
        if ci is None:
            continue
        by_key[(e.path, ci)] = (i, e.content_hash)

    final_entries = [_chunk_to_entry(rec, ch) for rec, ch in entries_proto]
    n = len(final_entries)
    reuse_pairs: list[tuple[int, int]] = []  # (final_pos, old_row_index)
    to_embed: list[tuple[int, FileRecord, Chunk]] = []
    n_unchanged = n_changed = n_new = 0
    for pos, ((rec, chunk), entry) in enumerate(zip(entries_proto, final_entries)):
        key = (rec.path, chunk.chunk_index)
        prev = by_key.get(key)
        if prev is not None and prev[1] == entry.content_hash:
            reuse_pairs.append((pos, prev[0]))
            n_unchanged += 1
        else:
            to_embed.append((pos, rec, chunk))
            if prev is None:
                n_new += 1
            else:
                n_changed += 1

    n_removed = len(existing.entries) - n_unchanged - n_changed
    logger.info(
        "Incremental rebuild: %d unchanged, %d changed, %d new, %d removed "
        "(out of %d existing chunks)",
        n_unchanged, n_changed, n_new, n_removed, len(existing.entries),
    )

    dim = existing.dimension
    if n == 0:
        return VectorIndex(
            vectors=_np.empty((0, dim), dtype=_np.float32),
            entries=[],
            model_name=config.model_name,
            dimension=dim,
            chunk_config=_chunk_config_to_dict(chunk_cfg),
        )

    if to_embed:
        model = EmbeddingModel(config)
        dim = model.dimension
    vectors = _np.zeros((n, dim), dtype=_np.float32)
    for final_pos, old_row in reuse_pairs:
        vectors[final_pos] = existing.vectors[old_row]
    if to_embed:
        texts = [build_enriched_chunk_text(rec, ch) for _, rec, ch in to_embed]
        started = perf_counter()
        new_vecs = model.encode_documents(texts, show_progress=len(texts) > 32)
        elapsed = perf_counter() - started
        logger.info("Embedded %d chunks in %.1f s (incremental)", len(texts), elapsed)
        for (final_pos, _, _), v in zip(to_embed, new_vecs):
            vectors[final_pos] = v

    return VectorIndex(
        vectors=vectors,
        entries=final_entries,
        model_name=config.model_name,
        dimension=dim,
        chunk_config=_chunk_config_to_dict(chunk_cfg),
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
