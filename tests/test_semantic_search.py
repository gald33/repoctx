"""Tests for the ``semantic_search`` op."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

numpy = pytest.importorskip("numpy")

from repoctx.embeddings import EmbeddingRetriever
from repoctx.ops.semantic_search import op_semantic_search
from repoctx.vector_index import IndexEntry, VectorIndex


def _normalised(vec: list[float]) -> "numpy.ndarray":
    a = numpy.array(vec, dtype=numpy.float32)
    norm = numpy.linalg.norm(a)
    return a if norm == 0 else a / norm


class _FakeModel:
    """Stand-in for EmbeddingModel: maps preset queries to fixed unit vectors."""

    def __init__(self, query_vec: "numpy.ndarray") -> None:
        self._query_vec = query_vec

    def encode_query(self, text: str) -> "numpy.ndarray":  # noqa: ARG002
        return self._query_vec


def _build_repo_with_index(
    repo: Path,
    *,
    files: dict[str, str],
    chunks: list[dict],
    query_vec: "numpy.ndarray",
) -> "numpy.ndarray":
    """Materialise *files* on disk and build an in-memory VectorIndex.

    *chunks* is a list of dicts: ``{path, kind, vec, start_line, end_line,
    enclosing_symbol}``. Returns the matching retriever's ``query_vec``.
    """
    for rel, content in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    vectors = numpy.stack([c["vec"] for c in chunks])
    entries = [
        IndexEntry(
            path=c["path"],
            kind=c["kind"],
            content_hash=f"h{i}",
            record_type="chunk",
            metadata={
                "chunk_index": i,
                "start_line": c["start_line"],
                "end_line": c["end_line"],
                "enclosing_symbol": c.get("enclosing_symbol"),
            },
        )
        for i, c in enumerate(chunks)
    ]
    index = VectorIndex(
        vectors=vectors, entries=entries, model_name="fake", dimension=vectors.shape[1],
    )
    cfg_dir = repo / ".repoctx" / "embeddings"
    index.save(cfg_dir)
    return query_vec


def _patched_retriever(repo: Path, query_vec: "numpy.ndarray"):
    """Patch try_load_retriever to return a retriever using a fake model."""
    from repoctx.vector_index import VectorIndex as _VI

    loaded = _VI.load(repo / ".repoctx" / "embeddings")
    retriever = EmbeddingRetriever(model=_FakeModel(query_vec), index=loaded)
    return patch(
        "repoctx.ops.semantic_search.try_load_retriever",
        return_value=retriever,
    )


# -- empty / cold-start -------------------------------------------------------


def test_returns_empty_list_when_no_index(tmp_path: Path, caplog) -> None:
    """No index built yet → empty list, with an info-level log message."""
    caplog.set_level("INFO", logger="repoctx.ops.semantic_search")
    hits = op_semantic_search("anything", repo_root=tmp_path, top_k=5)
    assert hits == []
    assert any("no embedding index" in r.getMessage().lower() for r in caplog.records)


def test_returns_empty_list_when_top_k_zero(tmp_path: Path) -> None:
    assert op_semantic_search("q", repo_root=tmp_path, top_k=0) == []


# -- ranking & filtering ------------------------------------------------------


def _three_chunk_repo(repo: Path):
    """One code file with 2 chunks, plus one doc file. Returns query_vec."""
    long_code = "\n".join(f"line {i}" for i in range(1, 21)) + "\n"
    files = {
        "src/auth.py": long_code,
        "docs/auth.md": "Auth doc paragraph one.\nAuth doc paragraph two.\n",
    }
    # 4-d unit vectors. Query points exactly at chunks[0], so:
    #   chunks[0] cos = 1.0  (top hit, code, lines 1-10)
    #   chunks[1] cos ≈ 0.71 (code, lines 11-20)
    #   chunks[2] cos = 0.0  (doc, lines 1-2)
    chunks = [
        {
            "path": "src/auth.py", "kind": "code",
            "vec": _normalised([1.0, 0.0, 0.0, 0.0]),
            "start_line": 1, "end_line": 10,
            "enclosing_symbol": "login",
        },
        {
            "path": "src/auth.py", "kind": "code",
            "vec": _normalised([1.0, 1.0, 0.0, 0.0]),
            "start_line": 11, "end_line": 20,
            "enclosing_symbol": "logout",
        },
        {
            "path": "docs/auth.md", "kind": "doc",
            "vec": _normalised([0.0, 0.0, 1.0, 0.0]),
            "start_line": 1, "end_line": 2,
            "enclosing_symbol": None,
        },
    ]
    query_vec = _normalised([1.0, 0.0, 0.0, 0.0])
    _build_repo_with_index(repo, files=files, chunks=chunks, query_vec=query_vec)
    return query_vec


def test_returns_top_k_sorted_descending(tmp_path: Path) -> None:
    query_vec = _three_chunk_repo(tmp_path)
    with _patched_retriever(tmp_path, query_vec):
        hits = op_semantic_search("login", repo_root=tmp_path, top_k=2)
    assert len(hits) == 2
    assert [h["score"] for h in hits] == sorted(
        [h["score"] for h in hits], reverse=True,
    )
    # Top hit is chunks[0] — exact match, score ≈ 1.0.
    assert hits[0]["path"] == "src/auth.py"
    assert hits[0]["start_line"] == 1
    assert hits[0]["end_line"] == 10
    assert hits[0]["enclosing_symbol"] == "login"
    assert hits[0]["score"] == pytest.approx(1.0, abs=1e-5)
    # Second hit is the other chunk in the same file.
    assert hits[1]["enclosing_symbol"] == "logout"
    assert hits[1]["score"] < hits[0]["score"]


def test_top_k_caps_result_count(tmp_path: Path) -> None:
    query_vec = _three_chunk_repo(tmp_path)
    with _patched_retriever(tmp_path, query_vec):
        hits = op_semantic_search("auth", repo_root=tmp_path, top_k=1)
    assert len(hits) == 1


def test_kind_filter_narrows_results(tmp_path: Path) -> None:
    query_vec = _three_chunk_repo(tmp_path)
    with _patched_retriever(tmp_path, query_vec):
        code_hits = op_semantic_search(
            "auth", repo_root=tmp_path, top_k=10, kind="code",
        )
        doc_hits = op_semantic_search(
            "auth", repo_root=tmp_path, top_k=10, kind="doc",
        )
    assert len(code_hits) == 2
    assert {h["path"] for h in code_hits} == {"src/auth.py"}
    assert len(doc_hits) == 1
    assert doc_hits[0]["path"] == "docs/auth.md"


def test_kind_filter_unknown_value_ignored(tmp_path: Path) -> None:
    """Bogus kind silently falls back to no filter (with a warning log)."""
    query_vec = _three_chunk_repo(tmp_path)
    with _patched_retriever(tmp_path, query_vec):
        hits = op_semantic_search(
            "auth", repo_root=tmp_path, top_k=10, kind="bogus",
        )
    assert len(hits) == 3


# -- snippets ------------------------------------------------------------------


def test_snippet_contains_claimed_line_range(tmp_path: Path) -> None:
    query_vec = _three_chunk_repo(tmp_path)
    with _patched_retriever(tmp_path, query_vec):
        hits = op_semantic_search("auth", repo_root=tmp_path, top_k=3)

    by_lines = {(h["start_line"], h["end_line"]): h for h in hits}
    # First chunk: lines 1-10 of src/auth.py.
    first = by_lines[(1, 10)]
    assert "line 1\n" in first["snippet"]
    assert "line 10\n" in first["snippet"]
    # Should not bleed into line 11 (which belongs to the next chunk).
    assert "line 11" not in first["snippet"]

    # Second chunk: lines 11-20.
    second = by_lines[(11, 20)]
    assert "line 11\n" in second["snippet"]
    assert "line 20\n" in second["snippet"]
    assert "line 1\n" not in second["snippet"]


def test_snippet_truncated_to_max_chars(tmp_path: Path) -> None:
    """A long chunk should have its snippet truncated to ~snippet_chars."""
    long_text = "x" * 5000 + "\n"
    files = {"big.py": long_text}
    chunks = [
        {
            "path": "big.py", "kind": "code",
            "vec": _normalised([1.0, 0.0, 0.0, 0.0]),
            "start_line": 1, "end_line": 1,
            "enclosing_symbol": None,
        },
    ]
    query_vec = _normalised([1.0, 0.0, 0.0, 0.0])
    _build_repo_with_index(tmp_path, files=files, chunks=chunks, query_vec=query_vec)
    with _patched_retriever(tmp_path, query_vec):
        hits = op_semantic_search("big", repo_root=tmp_path, top_k=1)
    assert len(hits[0]["snippet"]) <= 500


def test_missing_source_file_returns_empty_snippet(tmp_path: Path) -> None:
    """If a file was renamed/deleted after indexing, snippet is empty (not an error)."""
    files = {"present.py": "hello\nworld\n"}
    chunks = [
        {
            "path": "missing.py", "kind": "code",
            "vec": _normalised([1.0, 0.0, 0.0, 0.0]),
            "start_line": 1, "end_line": 5,
            "enclosing_symbol": None,
        },
    ]
    query_vec = _normalised([1.0, 0.0, 0.0, 0.0])
    _build_repo_with_index(tmp_path, files=files, chunks=chunks, query_vec=query_vec)
    with _patched_retriever(tmp_path, query_vec):
        hits = op_semantic_search("anything", repo_root=tmp_path, top_k=1)
    assert hits[0]["path"] == "missing.py"
    assert hits[0]["snippet"] == ""
