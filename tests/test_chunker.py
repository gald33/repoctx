"""Tests for repoctx.chunker.chunk_record."""

from pathlib import Path

import pytest

from repoctx.chunker import (
    Chunk,
    ChunkConfig,
    chunk_record,
    estimate_tokens,
)
from repoctx.models import FileRecord
from repoctx.symbols import Symbol, extract_symbols


def _record(content: str, kind: str = "code", ext: str = ".py") -> FileRecord:
    return FileRecord(
        path=f"x{ext}",
        absolute_path=Path(f"/tmp/x{ext}"),
        extension=ext,
        kind=kind,  # type: ignore[arg-type]
        content=content,
    )


# ---------- basics ------------------------------------------------------------


def test_empty_content_returns_empty():
    assert chunk_record(_record("")) == []


def test_short_file_yields_single_chunk():
    src = "def foo():\n    return 1\n"
    chunks = chunk_record(_record(src), symbols=extract_symbols(_record(src)))
    assert len(chunks) == 1
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 2
    assert chunks[0].chunk_index == 0
    assert chunks[0].enclosing_symbol == "foo"


def test_estimate_tokens_word_proxy():
    assert estimate_tokens("") == 0
    # 10 words → 13 tokens by × 1.3 rule.
    assert estimate_tokens(" ".join(["word"] * 10)) == 13


def test_chunk_indices_are_sequential():
    src = "\n".join(f"line {i} of content" for i in range(400))
    chunks = chunk_record(_record(src, kind="doc", ext=".md"))
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


# ---------- code mode: symbol-aware splits -----------------------------------


def test_code_breaks_at_top_level_function():
    # Two functions, each padded so the file exceeds target_tokens and the
    # walker has to choose a boundary. The boundary should land between them.
    body = "\n".join(["    x = 1"] * 60)
    src = f"def foo():\n{body}\n\ndef bar():\n{body}\n"
    record = _record(src)
    symbols = extract_symbols(record)
    chunks = chunk_record(record, symbols=symbols, cfg=ChunkConfig(
        target_tokens=80, max_tokens=200, overlap_tokens=0, min_tokens=10,
    ))
    assert len(chunks) >= 2
    # First chunk should belong to foo, a later chunk to bar.
    enclosing = [c.enclosing_symbol for c in chunks]
    assert "foo" in enclosing
    assert "bar" in enclosing
    # First boundary should be on a top-level def line — check that no chunk
    # straddles the foo→bar handoff (i.e. no chunk contains both).
    for c in chunks:
        text = c.text
        # Either the chunk has foo or bar, never both top-level defs.
        assert not ("def foo" in text and "def bar" in text)


def test_code_module_level_lines_get_no_enclosing_symbol():
    # Mostly module-level (5 lines) with a small function (2 lines). When
    # module-level dominates the chunk by line count, enclosing should be None;
    # this confirms _dominant_symbol uses raw count, not non-None preference,
    # except as a tie-breaker.
    src = (
        "import os\n"
        "import sys\n"
        "\n"
        "CONSTANT = 1\n"
        "\n"
        "def foo():\n"
        "    return CONSTANT\n"
    )
    record = _record(src)
    chunks = chunk_record(record, symbols=extract_symbols(record))
    assert len(chunks) == 1
    assert chunks[0].enclosing_symbol is None


def test_code_pure_module_level_yields_none_enclosing():
    src = "\n".join([f"X{i} = {i}" for i in range(40)])
    record = _record(src)
    chunks = chunk_record(record, symbols=[])
    assert chunks
    assert all(c.enclosing_symbol is None for c in chunks)


def test_long_class_splits_by_methods():
    body = "\n".join(["        x = 1"] * 30)
    src = (
        "class Foo:\n"
        + f"    def bar(self):\n{body}\n"
        + f"    def baz(self):\n{body}\n"
        + f"    def qux(self):\n{body}\n"
    )
    record = _record(src)
    symbols = extract_symbols(record)
    chunks = chunk_record(record, symbols=symbols, cfg=ChunkConfig(
        target_tokens=60, max_tokens=140, overlap_tokens=0, min_tokens=10,
    ))
    assert len(chunks) >= 2
    enclosing = {c.enclosing_symbol for c in chunks}
    # Each method should appear as the enclosing symbol of at least one chunk,
    # confirming nested-symbol boundaries were preferred.
    assert "Foo.bar" in enclosing
    assert {"Foo.baz", "Foo.qux"} & enclosing


# ---------- prose mode: paragraph-aware splits -------------------------------


def test_prose_breaks_at_paragraph_boundary():
    para1 = " ".join(["alpha"] * 60)
    para2 = " ".join(["beta"] * 60)
    para3 = " ".join(["gamma"] * 60)
    src = f"{para1}\n\n{para2}\n\n{para3}\n"
    record = _record(src, kind="doc", ext=".md")
    chunks = chunk_record(record, cfg=ChunkConfig(
        target_tokens=70, max_tokens=200, overlap_tokens=0, min_tokens=10,
    ))
    assert len(chunks) >= 2
    # No chunk should contain text from all three paragraphs.
    for c in chunks:
        present = sum(token in c.text for token in ("alpha", "beta", "gamma"))
        assert present <= 2  # at most spans one boundary, and we forbid all-three


def test_prose_no_paragraph_falls_back_to_sentence():
    sentences = [f"Sentence number {i} has some words in it." for i in range(40)]
    src = " ".join(sentences) + "\n"
    record = _record(src, kind="doc", ext=".md")
    chunks = chunk_record(record, cfg=ChunkConfig(
        target_tokens=50, max_tokens=120, overlap_tokens=0, min_tokens=10,
    ))
    # Single line of text → no line-level boundaries; the walker can only
    # hard-cut at max_tokens. We at least expect a chunk to be produced.
    assert chunks
    # And a multi-line variant should split cleanly on sentence ends.
    src2 = "\n".join(sentences) + "\n"
    chunks2 = chunk_record(_record(src2, kind="doc", ext=".md"), cfg=ChunkConfig(
        target_tokens=50, max_tokens=120, overlap_tokens=0, min_tokens=10,
    ))
    assert len(chunks2) >= 2


# ---------- overlap -----------------------------------------------------------


def test_overlap_carries_lines_into_next_chunk():
    src = "\n".join(f"unique_token_{i}" for i in range(120)) + "\n"
    record = _record(src, kind="doc", ext=".md")
    chunks = chunk_record(record, cfg=ChunkConfig(
        target_tokens=30, max_tokens=80, overlap_tokens=20, min_tokens=5,
    ))
    assert len(chunks) >= 2
    # Overlap means consecutive chunks share line ranges:
    # next.start_line <= prev.end_line.
    for a, b in zip(chunks, chunks[1:]):
        assert b.start_line <= a.end_line, (
            f"expected overlap, got prev=[{a.start_line}-{a.end_line}] "
            f"next=[{b.start_line}-{b.end_line}]"
        )


def test_zero_overlap_no_repetition():
    src = "\n".join(f"tok_{i}" for i in range(100)) + "\n"
    record = _record(src, kind="doc", ext=".md")
    chunks = chunk_record(record, cfg=ChunkConfig(
        target_tokens=20, max_tokens=50, overlap_tokens=0, min_tokens=5,
    ))
    assert len(chunks) >= 2
    # End of chunk[i] line == start of chunk[i+1] line - 1.
    for a, b in zip(chunks, chunks[1:]):
        assert b.start_line == a.end_line + 1


# ---------- properties --------------------------------------------------------


def test_chunks_cover_file_under_zero_overlap():
    src = "\n".join(f"line {i}" for i in range(200)) + "\n"
    record = _record(src, kind="doc", ext=".md")
    chunks = chunk_record(record, cfg=ChunkConfig(
        target_tokens=30, max_tokens=80, overlap_tokens=0, min_tokens=5,
    ))
    # Spans concatenate to cover [1, n].
    assert chunks[0].start_line == 1
    for a, b in zip(chunks, chunks[1:]):
        assert b.start_line == a.end_line + 1
    # Last chunk reaches the last non-empty line (we strip the trailing newline).
    assert chunks[-1].end_line >= 200


def test_chunk_dataclass_is_frozen():
    c = Chunk("x", 1, 1, None, 0)
    with pytest.raises(Exception):
        c.text = "y"  # type: ignore[misc]


def test_progress_guaranteed_on_pathological_input():
    # Single long line — walker must still terminate and produce >=1 chunk.
    src = "x " * 5000
    chunks = chunk_record(_record(src, kind="doc", ext=".md"), cfg=ChunkConfig(
        target_tokens=50, max_tokens=120, overlap_tokens=20, min_tokens=5,
    ))
    assert chunks  # didn't infinite-loop
