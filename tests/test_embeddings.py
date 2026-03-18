"""Tests for the embedding subsystem: enriched text, model wrapper, retriever."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from repoctx.config import DEFAULT_EMBEDDING_CONFIG, EmbeddingConfig
from repoctx.embeddings import (
    EmbeddingRetriever,
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
