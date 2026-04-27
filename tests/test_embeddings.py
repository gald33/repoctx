"""Tests for the embedding subsystem: enriched text, model wrapper, retriever."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from repoctx.chunker import Chunk
from repoctx.config import DEFAULT_EMBEDDING_CONFIG, EmbeddingConfig
from repoctx.embeddings import (
    EmbeddingRetriever,
    build_enriched_chunk_text,
    build_enriched_text,
    content_hash,
)
from repoctx.models import FileRecord


# -- enriched text -----------------------------------------------------------


def test_enriched_text_code_file() -> None:
    record = FileRecord(
        path="src/webhook/retry_policy.py",
        absolute_path=Path("/repo/src/webhook/retry_policy.py"),
        extension=".py",
        kind="code",
        content="def compute_retry_delay():\n    return 1\n",
    )
    text = build_enriched_text(record)
    assert "file: src/webhook/retry_policy.py" in text
    assert "kind: code" in text
    assert "module: src/webhook" in text
    assert "def compute_retry_delay" in text


def test_enriched_text_doc_file() -> None:
    record = FileRecord(
        path="AGENTS.md",
        absolute_path=Path("/repo/AGENTS.md"),
        extension=".md",
        kind="doc",
        content="# Agent guidance\nFollow the plan.",
        doc_score=12.0,
    )
    text = build_enriched_text(record)
    assert "file: AGENTS.md" in text
    assert "kind: doc" in text
    assert "module:" not in text  # root-level, no module
    assert "Agent guidance" in text


def test_enriched_text_nested_doc() -> None:
    record = FileRecord(
        path="services/billing/AGENT.md",
        absolute_path=Path("/repo/services/billing/AGENT.md"),
        extension=".md",
        kind="doc",
        content="Billing agent doc.",
        doc_score=10.0,
    )
    text = build_enriched_text(record)
    assert "module: services/billing" in text


def test_enriched_text_truncates_content() -> None:
    record = FileRecord(
        path="big.py",
        absolute_path=Path("/repo/big.py"),
        extension=".py",
        kind="code",
        content="x" * 20_000,
    )
    text = build_enriched_text(record, max_content_chars=100)
    assert len(text) < 200


def test_enriched_text_empty_content() -> None:
    record = FileRecord(
        path="empty.py",
        absolute_path=Path("/repo/empty.py"),
        extension=".py",
        kind="code",
        content="",
    )
    text = build_enriched_text(record)
    assert "file: empty.py" in text


# -- content_hash -----------------------------------------------------------


def test_content_hash_deterministic() -> None:
    h1 = content_hash("hello world")
    h2 = content_hash("hello world")
    assert h1 == h2
    assert len(h1) == 16


def test_content_hash_differs() -> None:
    assert content_hash("a") != content_hash("b")


# -- EmbeddingRetriever (with mock model) ------------------------------------


def test_retriever_query_scores() -> None:
    """EmbeddingRetriever delegates to model.encode_query + index.similarity_scores."""
    mock_model = MagicMock()
    mock_model.encode_query.return_value = "fake_vector"

    mock_index = MagicMock()
    mock_index.similarity_scores.return_value = {"a.py": 0.9, "b.py": 0.3}

    retriever = EmbeddingRetriever(model=mock_model, index=mock_index)
    scores = retriever.query_scores("fix the bug")

    mock_model.encode_query.assert_called_once_with("fix the bug")
    mock_index.similarity_scores.assert_called_once_with("fake_vector")
    assert scores == {"a.py": 0.9, "b.py": 0.3}


# -- try_load_retriever fallback --------------------------------------------


def test_try_load_retriever_returns_none_when_no_index(tmp_path: Path) -> None:
    from repoctx.embeddings import try_load_retriever

    result = try_load_retriever(tmp_path)
    assert result is None


def test_try_load_retriever_returns_none_without_deps() -> None:
    from repoctx.embeddings import try_load_retriever

    with patch("repoctx.embeddings.HAS_EMBEDDINGS", False):
        result = try_load_retriever(Path("/nonexistent"))
    assert result is None


# -- EmbeddingModel (integration guard) --------------------------------------


def test_embedding_model_requires_deps() -> None:
    from repoctx.embeddings import EmbeddingModel

    with patch("repoctx.embeddings.HAS_EMBEDDINGS", False):
        with pytest.raises(ImportError, match="sentence-transformers"):
            EmbeddingModel()


# -- device + batch resolution & MPS fallback --------------------------------


class _SpyTokenizer:
    model_max_length = 8192


class _SpyModel:
    """Stub that records encode calls and lets tests inject a failure."""

    def __init__(self, device: str = "cpu", *, fail_first_n: int = 0) -> None:
        self.device = device
        self._fail_remaining = fail_first_n
        self.encode_calls: list[dict] = []
        self.max_seq_length = 8192  # default; overwritten by _apply_dtype_and_seq_length
        self.dtype = "fp32"
        self.tokenizer = _SpyTokenizer()

    def get_sentence_embedding_dimension(self) -> int:
        return 8

    def encode(self, texts, **kwargs):
        import numpy as np

        self.encode_calls.append({"device": self.device, "n": len(texts) if not isinstance(texts, str) else 1, **kwargs})
        if self._fail_remaining > 0:
            self._fail_remaining -= 1
            raise RuntimeError("simulated device OOM")
        if isinstance(texts, str):
            return np.zeros(8, dtype=np.float32)
        return np.zeros((len(texts), 8), dtype=np.float32)

    def to(self, device):
        self.device = device
        return self

    def half(self):
        self.dtype = "fp16"
        return self

    def float(self):
        self.dtype = "fp32"
        return self


def _patch_st(spy: _SpyModel):
    """Patch SentenceTransformer to return *spy* and HAS_EMBEDDINGS to True."""
    return patch.multiple(
        "repoctx.embeddings",
        HAS_EMBEDDINGS=True,
        SentenceTransformer=lambda *a, **kw: spy,
    )


def test_mps_clamps_batch_size_to_safe_default() -> None:
    """When device resolves to MPS and configured batch > 8, we clamp."""
    from repoctx.embeddings import EmbeddingModel, _MPS_MAX_BATCH

    spy = _SpyModel(device="mps")
    cfg = EmbeddingConfig(batch_size=32, device="mps")
    with _patch_st(spy):
        m = EmbeddingModel(cfg)
    assert m.batch_size == _MPS_MAX_BATCH
    assert m._device == "mps"


def test_mps_does_not_clamp_when_user_explicitly_smaller() -> None:
    """If the user already picked a small batch, we don't bump it up."""
    from repoctx.embeddings import EmbeddingModel

    spy = _SpyModel(device="mps")
    cfg = EmbeddingConfig(batch_size=2, device="mps")
    with _patch_st(spy):
        m = EmbeddingModel(cfg)
    assert m.batch_size == 2


def test_cpu_device_never_clamps() -> None:
    from repoctx.embeddings import EmbeddingModel

    spy = _SpyModel(device="cpu")
    cfg = EmbeddingConfig(batch_size=64, device="cpu")
    with _patch_st(spy):
        m = EmbeddingModel(cfg)
    assert m.batch_size == 64


def test_encode_documents_falls_back_to_cpu_on_runtime_error() -> None:
    from repoctx.embeddings import EmbeddingModel

    spy = _SpyModel(device="mps", fail_first_n=1)
    cfg = EmbeddingConfig(device="mps")
    with _patch_st(spy):
        m = EmbeddingModel(cfg)
        out = m.encode_documents(["hello", "world"], show_progress=False)
    assert out.shape == (2, 8)
    # Two encode calls: one on MPS that failed, one retry on CPU.
    assert len(spy.encode_calls) == 2
    assert spy.encode_calls[0]["device"] == "mps"
    assert spy.encode_calls[1]["device"] == "cpu"
    assert m._device == "cpu"


def test_encode_documents_does_not_loop_when_cpu_also_fails() -> None:
    """If CPU is the active device and it errors, the exception propagates."""
    from repoctx.embeddings import EmbeddingModel

    spy = _SpyModel(device="cpu", fail_first_n=1)
    cfg = EmbeddingConfig(device="cpu")
    with _patch_st(spy):
        m = EmbeddingModel(cfg)
        with pytest.raises(RuntimeError, match="simulated"):
            m.encode_documents(["hello"], show_progress=False)
    assert len(spy.encode_calls) == 1


def test_env_vars_override_config(monkeypatch) -> None:
    from repoctx.embeddings import EmbeddingModel

    monkeypatch.setenv("REPOCTX_EMBEDDING_DEVICE", "cpu")
    monkeypatch.setenv("REPOCTX_EMBEDDING_BATCH_SIZE", "3")

    spy = _SpyModel(device="cpu")
    cfg = EmbeddingConfig(device="mps", batch_size=32)  # config says mps/32
    with _patch_st(spy):
        m = EmbeddingModel(cfg)
    assert m.batch_size == 3  # env wins over config
    assert m._device == "cpu"


def test_encode_query_falls_back_too() -> None:
    from repoctx.embeddings import EmbeddingModel

    spy = _SpyModel(device="mps", fail_first_n=1)
    cfg = EmbeddingConfig(device="mps")
    with _patch_st(spy):
        m = EmbeddingModel(cfg)
        v = m.encode_query("hello")
    assert v.shape == (8,)
    assert m._device == "cpu"


# -- fp16 + max_seq_length + super-batch cache eviction ----------------------


def test_fp16_applied_on_mps_by_default() -> None:
    from repoctx.embeddings import EmbeddingModel

    spy = _SpyModel(device="mps")
    cfg = EmbeddingConfig(device="mps")  # dtype="auto"
    with _patch_st(spy):
        m = EmbeddingModel(cfg)
    assert spy.dtype == "fp16"
    assert m._dtype == "fp16"


def test_fp32_default_on_cpu() -> None:
    from repoctx.embeddings import EmbeddingModel

    spy = _SpyModel(device="cpu")
    cfg = EmbeddingConfig(device="cpu")
    with _patch_st(spy):
        m = EmbeddingModel(cfg)
    assert spy.dtype == "fp32"
    assert m._dtype == "fp32"


def test_explicit_fp32_pin_overrides_auto() -> None:
    from repoctx.embeddings import EmbeddingModel

    spy = _SpyModel(device="mps")
    cfg = EmbeddingConfig(device="mps", dtype="fp32")
    with _patch_st(spy):
        m = EmbeddingModel(cfg)
    assert m._dtype == "fp32"
    assert spy.dtype == "fp32"


def test_explicit_fp16_pin_on_cpu() -> None:
    from repoctx.embeddings import EmbeddingModel

    spy = _SpyModel(device="cpu")
    cfg = EmbeddingConfig(device="cpu", dtype="fp16")
    with _patch_st(spy):
        m = EmbeddingModel(cfg)
    # User explicitly asked for fp16 even on CPU; honor it.
    assert m._dtype == "fp16"


def test_max_seq_length_applied() -> None:
    from repoctx.embeddings import EmbeddingModel

    spy = _SpyModel(device="cpu")
    cfg = EmbeddingConfig(device="cpu", max_seq_length=128)
    with _patch_st(spy):
        m = EmbeddingModel(cfg)
    assert spy.max_seq_length == 128
    assert m._model.max_seq_length == 128


def test_max_seq_length_caps_at_tokenizer_limit() -> None:
    from repoctx.embeddings import EmbeddingModel

    spy = _SpyModel(device="cpu")
    spy.tokenizer = _SpyTokenizer()
    spy.tokenizer.model_max_length = 64
    cfg = EmbeddingConfig(device="cpu", max_seq_length=512)
    with _patch_st(spy):
        m = EmbeddingModel(cfg)
    # Tokenizer limit (64) wins over config (512).
    assert spy.max_seq_length == 64


def test_max_seq_length_env_override(monkeypatch) -> None:
    from repoctx.embeddings import EmbeddingModel

    monkeypatch.setenv("REPOCTX_EMBEDDING_MAX_SEQ_LENGTH", "192")
    spy = _SpyModel(device="cpu")
    cfg = EmbeddingConfig(device="cpu", max_seq_length=512)
    with _patch_st(spy):
        m = EmbeddingModel(cfg)
    assert spy.max_seq_length == 192


def test_dtype_env_override(monkeypatch) -> None:
    from repoctx.embeddings import EmbeddingModel

    monkeypatch.setenv("REPOCTX_EMBEDDING_DTYPE", "fp32")
    spy = _SpyModel(device="mps")
    cfg = EmbeddingConfig(device="mps")  # would default to fp16
    with _patch_st(spy):
        m = EmbeddingModel(cfg)
    assert m._dtype == "fp32"


def test_super_batch_evicts_cache_between_groups() -> None:
    """On accelerators, large input is split into super-batches with
    empty_cache() called between groups."""
    from repoctx.embeddings import EmbeddingModel, _SUPER_BATCH_MULTIPLIER

    spy = _SpyModel(device="mps")
    cfg = EmbeddingConfig(device="mps", batch_size=4)
    with _patch_st(spy):
        m = EmbeddingModel(cfg)
        super_size = m.batch_size * _SUPER_BATCH_MULTIPLIER  # 4 * 8 = 32
        # Encode 80 texts → expect ceil(80/32) = 3 super-batches.
        out = m.encode_documents(["t"] * 80, show_progress=False)
    assert out.shape == (80, 8)
    # 3 encode calls (one per super-batch).
    assert len(spy.encode_calls) == 3
    sizes = [c["n"] for c in spy.encode_calls]
    assert sizes == [super_size, super_size, 80 - 2 * super_size]


def test_no_super_batch_on_cpu() -> None:
    """CPU path encodes in a single call regardless of input size."""
    from repoctx.embeddings import EmbeddingModel

    spy = _SpyModel(device="cpu")
    cfg = EmbeddingConfig(device="cpu", batch_size=4)
    with _patch_st(spy):
        m = EmbeddingModel(cfg)
        m.encode_documents(["t"] * 200, show_progress=False)
    assert len(spy.encode_calls) == 1
    assert spy.encode_calls[0]["n"] == 200


def test_super_batch_skipped_when_input_small() -> None:
    """If input fits in one super-batch, it's a single encode call."""
    from repoctx.embeddings import EmbeddingModel, _SUPER_BATCH_MULTIPLIER

    spy = _SpyModel(device="mps")
    cfg = EmbeddingConfig(device="mps", batch_size=4)
    with _patch_st(spy):
        m = EmbeddingModel(cfg)
        super_size = m.batch_size * _SUPER_BATCH_MULTIPLIER
        m.encode_documents(["t"] * (super_size - 1), show_progress=False)
    assert len(spy.encode_calls) == 1


def test_cpu_fallback_recasts_to_fp32() -> None:
    """When falling back from MPS+fp16 to CPU, dtype goes back to fp32."""
    from repoctx.embeddings import EmbeddingModel

    spy = _SpyModel(device="mps", fail_first_n=1)
    cfg = EmbeddingConfig(device="mps")
    with _patch_st(spy):
        m = EmbeddingModel(cfg)
        assert m._dtype == "fp16"
        m.encode_documents(["a", "b"], show_progress=False)
    assert m._device == "cpu"
    assert m._dtype == "fp32"
    assert spy.dtype == "fp32"


# -- enriched chunk text -----------------------------------------------------


def test_enriched_chunk_text_includes_symbol_and_lines() -> None:
    record = FileRecord(
        path="src/auth/service.py",
        absolute_path=Path("/repo/src/auth/service.py"),
        extension=".py",
        kind="code",
        content="def login():\n    pass\n",
    )
    chunk = Chunk(
        text="def login():\n    pass",
        start_line=42,
        end_line=87,
        enclosing_symbol="AuthService.login",
        chunk_index=3,
    )
    text = build_enriched_chunk_text(record, chunk)
    assert "file: src/auth/service.py" in text
    assert "kind: code" in text
    assert "module: src/auth" in text
    assert "symbol: AuthService.login" in text
    assert "lines: 42-87" in text
    assert "def login()" in text


def test_enriched_chunk_text_omits_symbol_when_none() -> None:
    record = FileRecord(
        path="x.py",
        absolute_path=Path("/repo/x.py"),
        extension=".py",
        kind="code",
        content="X = 1\n",
    )
    chunk = Chunk(text="X = 1", start_line=1, end_line=1, enclosing_symbol=None, chunk_index=0)
    text = build_enriched_chunk_text(record, chunk)
    assert "symbol:" not in text
    assert "lines: 1-1" in text


# -- build_index orchestration (model mocked) --------------------------------


class _FakeModel:
    """Stand-in for EmbeddingModel that returns deterministic vectors."""

    def __init__(self) -> None:
        import numpy as np

        self._np = np
        self.dimension = 8
        self.encoded_texts: list[list[str]] = []

    def encode_documents(self, texts, *, show_progress=True):
        self.encoded_texts.append(list(texts))
        return self._np.eye(len(texts), self.dimension, dtype=self._np.float32)

    def encode_query(self, text):
        return self._np.zeros(self.dimension, dtype=self._np.float32)


def test_build_index_emits_multiple_chunks_per_long_file(tmp_path: Path) -> None:
    """A long file should produce >1 chunks, all stored under the same path."""
    pytest.importorskip("numpy")
    repo = tmp_path / "repo"
    repo.mkdir()
    # Long Python file: two functions of ~80 lines each.
    body = "\n".join("    x = 1" for _ in range(80))
    (repo / "long.py").write_text(
        f"def foo():\n{body}\n\ndef bar():\n{body}\n"
    )

    fake = _FakeModel()
    with patch("repoctx.embeddings.EmbeddingModel", return_value=fake):
        from repoctx.chunker import ChunkConfig
        from repoctx.embeddings import build_index

        idx = build_index(
            repo,
            chunk_config=ChunkConfig(
                target_tokens=80, max_tokens=200, overlap_tokens=0, min_tokens=10,
            ),
        )

    # Multiple chunks for long.py — confirm via entry count and shared path.
    paths = [e.path for e in idx.entries]
    assert paths.count("long.py") >= 2
    # Each entry tagged as a chunk with span metadata.
    for e in idx.entries:
        assert e.record_type == "chunk"
        assert "chunk_index" in e.metadata
        assert "start_line" in e.metadata
        assert "end_line" in e.metadata
    # chunk_index should be sequential within a path.
    long_meta = [e.metadata for e in idx.entries if e.path == "long.py"]
    assert sorted(m["chunk_index"] for m in long_meta) == list(range(len(long_meta)))


def test_update_file_in_index_replaces_all_chunks(tmp_path: Path) -> None:
    pytest.importorskip("numpy")
    repo = tmp_path / "repo"
    repo.mkdir()
    body = "\n".join("    x = 1" for _ in range(80))
    long_py = repo / "long.py"
    long_py.write_text(f"def foo():\n{body}\n\ndef bar():\n{body}\n")

    fake = _FakeModel()
    with patch("repoctx.embeddings.EmbeddingModel", return_value=fake):
        from repoctx.chunker import ChunkConfig
        from repoctx.embeddings import build_index, update_file_in_index

        cfg = EmbeddingConfig()
        chunk_cfg = ChunkConfig(
            target_tokens=80, max_tokens=200, overlap_tokens=0, min_tokens=10,
        )
        idx = build_index(repo, config=cfg, chunk_config=chunk_cfg)
        index_dir = repo / cfg.index_dir / "embeddings"
        idx.save(index_dir)
        before = sum(1 for e in idx.entries if e.path == "long.py")
        assert before >= 2

        # Shrink the file dramatically — fewer chunks expected.
        long_py.write_text("def foo():\n    return 1\n")
        update_file_in_index("long.py", repo, config=cfg, chunk_config=chunk_cfg)

        from repoctx.vector_index import VectorIndex
        reloaded = VectorIndex.load(index_dir)
        after = sum(1 for e in reloaded.entries if e.path == "long.py")
        assert after >= 1
        assert after < before  # old chunks were removed
