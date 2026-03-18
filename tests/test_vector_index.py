"""Tests for the persistent vector index."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

numpy = pytest.importorskip("numpy")

from repoctx.vector_index import IndexEntry, VectorIndex


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
