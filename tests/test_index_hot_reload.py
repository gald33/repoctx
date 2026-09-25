"""A long-lived retriever follows the index on disk; a torn read is refused, not served.

Before 1.17.0 the MCP server loaded the index once and served those vectors for
its whole life: a background `repoctx index --refresh` reached the CLI and never
the MCP tools, so an agent's bundles stayed as stale as the snapshot the session
started with.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

numpy = pytest.importorskip("numpy")

from repoctx.embeddings import EmbeddingRetriever  # noqa: E402
from repoctx.vector_index import IndexEntry, VectorIndex  # noqa: E402


def _index(paths: list[str], model: str = "m") -> VectorIndex:
    vecs = numpy.eye(len(paths), 4, dtype=numpy.float32)
    entries = [IndexEntry(path=p, kind="code", content_hash=f"h{i}") for i, p in enumerate(paths)]
    return VectorIndex(vectors=vecs, entries=entries, model_name=model, dimension=4)


def _bump(path: Path) -> None:
    """Make a rewrite visible to the stamp even on a coarse-mtime filesystem."""
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def test_retriever_picks_up_a_finished_save(tmp_path: Path) -> None:
    _index(["a.py"]).save(tmp_path)
    retriever = EmbeddingRetriever(model=object(), index=VectorIndex.load(tmp_path), index_dir=tmp_path)

    assert retriever.refresh_index() is False, "nothing changed: no reload"

    _index(["a.py", "b.py"]).save(tmp_path)
    _bump(tmp_path / "index_config.json")

    assert retriever.refresh_index() is True
    assert [e.path for e in retriever.index.entries] == ["a.py", "b.py"]


def test_a_torn_save_keeps_the_loaded_index(tmp_path: Path) -> None:
    _index(["a.py"]).save(tmp_path)
    retriever = EmbeddingRetriever(model=object(), index=VectorIndex.load(tmp_path), index_dir=tmp_path)

    # vectors from a two-entry save next to one-entry metadata: a read mid-write.
    numpy.save(tmp_path / "vectors.npy", numpy.eye(2, 4, dtype=numpy.float32))
    _bump(tmp_path / "index_config.json")

    assert retriever.refresh_index() is False
    assert [e.path for e in retriever.index.entries] == ["a.py"]


def test_load_refuses_mismatched_vectors_and_metadata(tmp_path: Path) -> None:
    _index(["a.py"]).save(tmp_path)
    numpy.save(tmp_path / "vectors.npy", numpy.eye(2, 4, dtype=numpy.float32))

    with pytest.raises(ValueError, match="inconsistent"):
        VectorIndex.load(tmp_path)


def test_an_index_built_with_another_model_is_not_swapped_in(tmp_path: Path) -> None:
    _index(["a.py"]).save(tmp_path)
    retriever = EmbeddingRetriever(model=object(), index=VectorIndex.load(tmp_path), index_dir=tmp_path)

    _index(["a.py", "b.py"], model="other").save(tmp_path)
    _bump(tmp_path / "index_config.json")

    assert retriever.refresh_index() is False
    assert retriever.index.model_name == "m"
