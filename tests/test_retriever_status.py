"""RetrieverStatus distinguishes *why* embedding retrieval is unavailable."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

numpy = pytest.importorskip("numpy")

from repoctx.embeddings import (
    STATUS_DEPS_MISSING,
    STATUS_NO_INDEX,
    STATUS_OK,
    STATUS_SCHEMA_MISMATCH,
    load_retriever_status,
    probe_index_status,
)
from repoctx.index_location import shared_embeddings_dir
from repoctx.vector_index import IndexEntry, VectorIndex


def _save_index(d: Path) -> None:
    VectorIndex(
        vectors=numpy.eye(2, dtype=numpy.float32),
        entries=[
            IndexEntry(path="a.py", kind="code", content_hash="h0", record_type="chunk",
                       metadata={"chunk_index": 0, "start_line": 1, "end_line": 2}),
            IndexEntry(path="b.py", kind="code", content_hash="h1", record_type="chunk",
                       metadata={"chunk_index": 0, "start_line": 1, "end_line": 2}),
        ],
        model_name="fake", dimension=2,
    ).save(d)


def test_probe_no_index(tmp_path: Path) -> None:
    st = probe_index_status(tmp_path)
    assert st.status == STATUS_NO_INDEX
    assert not st.ok
    assert "repoctx index" in st.message


def test_probe_ok_when_index_present(tmp_path: Path) -> None:
    _save_index(shared_embeddings_dir(tmp_path))
    st = probe_index_status(tmp_path)
    assert st.status == STATUS_OK
    assert st.ok


def test_probe_schema_mismatch(tmp_path: Path) -> None:
    d = shared_embeddings_dir(tmp_path)
    _save_index(d)
    cfg = d / "index_config.json"
    payload = json.loads(cfg.read_text(encoding="utf-8"))
    payload["schema_version"] = 1  # pretend it's an old index
    cfg.write_text(json.dumps(payload), encoding="utf-8")
    st = probe_index_status(tmp_path)
    assert st.status == STATUS_SCHEMA_MISMATCH
    assert "rebuild" in st.message


def test_probe_deps_missing(tmp_path: Path) -> None:
    with patch("repoctx.embeddings.HAS_EMBEDDINGS", False):
        st = probe_index_status(tmp_path)
    assert st.status == STATUS_DEPS_MISSING


def test_load_retriever_status_no_index_does_not_construct_model(tmp_path: Path) -> None:
    """The cold-start path must not try to load the embedding model."""
    with patch("repoctx.embeddings.EmbeddingModel", side_effect=AssertionError("loaded model!")):
        st = load_retriever_status(tmp_path)
    assert st.status == STATUS_NO_INDEX
    assert st.retriever is None
