"""Tests for the persistent vector index."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

numpy = pytest.importorskip("numpy")

from repoctx.vector_index import (
    IndexEntry,
    IndexSchemaMismatch,
    SCHEMA_VERSION,
    VectorIndex,
)


def _normalise(v):
    """L2-normalise a list → numpy float32 array."""
    a = numpy.array(v, dtype=numpy.float32)
    return a / numpy.linalg.norm(a)


def _make_index(n: int = 3, dim: int = 4) -> VectorIndex:
    raw = [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
    ][:n]
    vecs = numpy.array(raw[:n], dtype=numpy.float32)
    entries = [
        IndexEntry(path=f"file_{i}.py", kind="code", content_hash=f"hash_{i}")
        for i in range(n)
    ]
    return VectorIndex(
        vectors=vecs,
        entries=entries,
        model_name="test-model",
        dimension=dim,
    )


# -- persistence round-trip ---------------------------------------------------


def test_save_and_load(tmp_path: Path) -> None:
    idx = _make_index()
    idx.save(tmp_path / "idx")
    loaded = VectorIndex.load(tmp_path / "idx")

    assert len(loaded) == len(idx)
    assert loaded.model_name == "test-model"
    assert loaded.dimension == 4
    assert loaded.entries[0].path == "file_0.py"
    numpy.testing.assert_allclose(loaded.vectors, idx.vectors, atol=1e-6)


def test_load_missing_dir(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Incomplete"):
        VectorIndex.load(tmp_path / "nonexistent")


def test_load_partial_dir(tmp_path: Path) -> None:
    d = tmp_path / "partial"
    d.mkdir()
    (d / "metadata.json").write_text("[]")
    with pytest.raises(FileNotFoundError, match="vectors.npy"):
        VectorIndex.load(d)


# -- similarity_scores --------------------------------------------------------


def test_similarity_scores_returns_all_paths() -> None:
    idx = _make_index()
    query = idx.vectors[0]  # unit vector [1,0,0,0]
    scores = idx.similarity_scores(query)
    assert set(scores.keys()) == {"file_0.py", "file_1.py", "file_2.py"}
    assert scores["file_0.py"] == pytest.approx(1.0, abs=1e-5)
    assert scores["file_1.py"] == pytest.approx(0.0, abs=1e-5)


def test_similarity_scores_empty_index() -> None:
    idx = VectorIndex()
    assert idx.similarity_scores(numpy.zeros(4)) == {}


# -- update_entry --------------------------------------------------------------


def test_update_existing_entry() -> None:
    idx = _make_index(n=2, dim=4)
    new_vec = _normalise([1.0, 1.0, 1.0, 1.0])
    idx.update_entry("file_0.py", "code", "new_hash", new_vec)

    assert len(idx) == 2
    assert idx.entries[0].content_hash == "new_hash"
    numpy.testing.assert_allclose(idx.vectors[0], new_vec, atol=1e-6)


def test_update_adds_new_entry() -> None:
    idx = _make_index(n=2, dim=4)
    new_vec = _normalise([0.0, 0.0, 0.0, 1.0])
    idx.update_entry("new_file.py", "code", "h", new_vec)

    assert len(idx) == 3
    assert idx.entries[-1].path == "new_file.py"
    numpy.testing.assert_allclose(idx.vectors[-1], new_vec, atol=1e-6)


def test_update_on_empty_index() -> None:
    idx = VectorIndex(dimension=4)
    vec = numpy.array([1.0, 0.0, 0.0, 0.0], dtype=numpy.float32)
    idx.update_entry("first.py", "code", "h", vec)
    assert len(idx) == 1


# -- chunk-aware multi-row API -------------------------------------------------


def _multi_chunk_index() -> VectorIndex:
    """Index with two chunks for file_a.py and one for file_b.py."""
    vecs = numpy.array(
        [
            [1.0, 0.0, 0.0, 0.0],  # file_a chunk 0
            [0.7, 0.7, 0.0, 0.0],  # file_a chunk 1 (different direction)
            [0.0, 0.0, 1.0, 0.0],  # file_b chunk 0
        ],
        dtype=numpy.float32,
    )
    # Normalise rows so dot product == cosine similarity.
    vecs /= numpy.linalg.norm(vecs, axis=1, keepdims=True)
    entries = [
        IndexEntry(
            path="file_a.py", kind="code", content_hash="h0",
            metadata={"chunk_index": 0, "start_line": 1, "end_line": 10},
        ),
        IndexEntry(
            path="file_a.py", kind="code", content_hash="h1",
            metadata={"chunk_index": 1, "start_line": 11, "end_line": 20},
        ),
        IndexEntry(
            path="file_b.py", kind="code", content_hash="h2",
            metadata={"chunk_index": 0, "start_line": 1, "end_line": 5},
        ),
    ]
    return VectorIndex(vectors=vecs, entries=entries, model_name="test", dimension=4)


def test_similarity_scores_max_pools_per_path() -> None:
    idx = _multi_chunk_index()
    # Query points exactly at file_a chunk 0; chunk 1 should have lower score.
    query = numpy.array([1.0, 0.0, 0.0, 0.0], dtype=numpy.float32)
    scores = idx.similarity_scores(query)
    # Two paths even though three rows exist.
    assert set(scores.keys()) == {"file_a.py", "file_b.py"}
    # file_a wins via chunk 0 (score = 1.0), not the 0.707 of chunk 1.
    assert scores["file_a.py"] == pytest.approx(1.0, abs=1e-5)
    assert scores["file_b.py"] == pytest.approx(0.0, abs=1e-5)


def test_similarity_scores_by_id_returns_all_chunks() -> None:
    idx = _multi_chunk_index()
    query = numpy.array([1.0, 0.0, 0.0, 0.0], dtype=numpy.float32)
    raw = idx.similarity_scores_by_id(query)
    # Three rows because per-chunk scoring doesn't aggregate.
    assert len(raw) == 3


def test_delete_by_path_removes_all_chunks() -> None:
    idx = _multi_chunk_index()
    removed = idx.delete_by_path("file_a.py")
    assert removed == 2
    assert len(idx) == 1
    assert idx.entries[0].path == "file_b.py"
    assert idx.vectors.shape == (1, 4)


def test_delete_by_path_missing_returns_zero() -> None:
    idx = _multi_chunk_index()
    assert idx.delete_by_path("not_in_index.py") == 0
    assert len(idx) == 3


def test_add_entries_appends_multiple_chunks() -> None:
    idx = _multi_chunk_index()
    new_vecs = numpy.array(
        [[0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0]], dtype=numpy.float32
    )
    new_entries = [
        IndexEntry(
            path="file_c.py", kind="code", content_hash="c0",
            metadata={"chunk_index": 0},
        ),
        IndexEntry(
            path="file_c.py", kind="code", content_hash="c1",
            metadata={"chunk_index": 1},
        ),
    ]
    idx.add_entries(new_entries, new_vecs)
    assert len(idx) == 5
    paths = [e.path for e in idx.entries]
    assert paths.count("file_c.py") == 2


def test_add_entries_to_empty_index() -> None:
    idx = VectorIndex(model_name="test", dimension=4)
    vecs = numpy.array([[1.0, 0.0, 0.0, 0.0]], dtype=numpy.float32)
    idx.add_entries(
        [IndexEntry(path="x.py", kind="code", content_hash="h")], vecs
    )
    assert len(idx) == 1
    assert idx.vectors.shape == (1, 4)


def test_add_entries_length_mismatch_raises() -> None:
    idx = VectorIndex(model_name="test", dimension=4)
    with pytest.raises(ValueError, match="entries but"):
        idx.add_entries(
            [IndexEntry(path="x", kind="code", content_hash="h")],
            numpy.zeros((2, 4), dtype=numpy.float32),
        )


# -- schema versioning ---------------------------------------------------------


def test_save_writes_schema_version(tmp_path: Path) -> None:
    import json

    idx = _make_index()
    idx.save(tmp_path / "idx")
    config = json.loads((tmp_path / "idx" / "index_config.json").read_text())
    assert config["schema_version"] == SCHEMA_VERSION


def test_load_rejects_v1_index(tmp_path: Path) -> None:
    """A pre-schema-versioning index should fail with a clear rebuild prompt."""
    import json

    d = tmp_path / "v1"
    idx = _make_index()
    idx.save(d)
    # Strip schema_version to simulate v1 layout.
    config_path = d / "index_config.json"
    config = json.loads(config_path.read_text())
    config.pop("schema_version", None)
    config_path.write_text(json.dumps(config))

    with pytest.raises(IndexSchemaMismatch, match="rebuild"):
        VectorIndex.load(d)


def test_save_writes_entry_and_file_counts(tmp_path: Path) -> None:
    """index_config.json reports both entry_count (chunks) and file_count (distinct paths)."""
    import json

    # Multi-chunk index: file_a has 2 chunks, file_b has 1 → 3 entries / 2 files.
    idx = _multi_chunk_index()
    idx.save(tmp_path / "idx")
    config = json.loads((tmp_path / "idx" / "index_config.json").read_text())
    assert config["entry_count"] == 3
    assert config["file_count"] == 2
    import json

    d = tmp_path / "v999"
    idx = _make_index()
    idx.save(d)
    config_path = d / "index_config.json"
    config = json.loads(config_path.read_text())
    config["schema_version"] = 999
    config_path.write_text(json.dumps(config))

    with pytest.raises(IndexSchemaMismatch):
        VectorIndex.load(d)
