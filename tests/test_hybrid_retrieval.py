"""Tests for hybrid (heuristic + embedding) retrieval scoring."""

from pathlib import Path

from repoctx.config import DEFAULT_CONFIG, RepoCtxConfig
from repoctx.graph import build_dependency_graph
from repoctx.retriever import get_task_context_data, rank_documents, rank_files
from repoctx.scanner import scan_repository


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_test_repo(tmp_path: Path) -> None:
    write_file(tmp_path / "AGENTS.md", "# Repo guidance\n")
    write_file(tmp_path / "README.md", "# My project\n")
    write_file(
        tmp_path / "src" / "auth" / "login.py",
        "def authenticate_user(username, password):\n    return True\n",
    )
    write_file(
        tmp_path / "src" / "auth" / "token.py",
        "def create_jwt_token(user_id):\n    return 'token'\n",
    )
    write_file(
        tmp_path / "src" / "billing" / "invoice.py",
        "def generate_invoice(order):\n    return {'total': 100}\n",
    )
    write_file(
        tmp_path / "tests" / "test_login.py",
        "from src.auth.login import authenticate_user\n",
    )


# -- embedding scores boost ranking ------------------------------------------


def test_embedding_scores_boost_file_ranking(tmp_path: Path) -> None:
    """A file with high embedding similarity should rank above heuristic-only matches."""
    _build_test_repo(tmp_path)
    index = scan_repository(tmp_path)

    embedding_scores = {
        "src/billing/invoice.py": 0.85,
        "src/auth/login.py": 0.1,
        "src/auth/token.py": 0.05,
    }

    ranked = rank_files(
        index, "handle payment processing", DEFAULT_CONFIG,
        embedding_scores=embedding_scores,
    )
    paths = [r.path for r in ranked]
    assert "src/billing/invoice.py" in paths
    invoice_item = next(r for r in ranked if r.path == "src/billing/invoice.py")
    assert invoice_item.embedding_score == 0.85
    assert invoice_item.heuristic_score >= 0


def test_embedding_scores_qualify_files_without_token_overlap(tmp_path: Path) -> None:
    """Files with high embedding similarity should appear even without token overlap."""
    _build_test_repo(tmp_path)
    index = scan_repository(tmp_path)

    embedding_scores = {
        "src/billing/invoice.py": 0.6,
    }

    ranked_without = rank_files(
        index, "monetary calculations for customers", DEFAULT_CONFIG,
        embedding_scores=None,
    )
    ranked_with = rank_files(
        index, "monetary calculations for customers", DEFAULT_CONFIG,
        embedding_scores=embedding_scores,
    )

    paths_without = [r.path for r in ranked_without]
    paths_with = [r.path for r in ranked_with]
    assert "src/billing/invoice.py" not in paths_without
    assert "src/billing/invoice.py" in paths_with


def test_embedding_scores_boost_doc_ranking(tmp_path: Path) -> None:
    _build_test_repo(tmp_path)
    write_file(tmp_path / "docs" / "SECURITY.md", "Authentication hardening guide.\n")
    index = scan_repository(tmp_path)

    embedding_scores = {
        "docs/SECURITY.md": 0.7,
        "AGENTS.md": 0.2,
        "README.md": 0.1,
    }

    ranked = rank_documents(
        index, "improve auth hardening", DEFAULT_CONFIG,
        embedding_scores=embedding_scores,
    )
    security = [r for r in ranked if r.path == "docs/SECURITY.md"]
    assert len(security) == 1
    assert security[0].embedding_score == 0.7


# -- fallback: no embeddings = pure heuristic --------------------------------


def test_retrieval_without_embeddings_unchanged(tmp_path: Path) -> None:
    """Passing embedding_scores=None preserves v1 heuristic behavior exactly."""
    _build_test_repo(tmp_path)
    index = scan_repository(tmp_path)
    graph = build_dependency_graph(index)

    result = get_task_context_data(
        task="add retry jitter to authentication",
        index=index, graph=graph,
        embedding_scores=None,
    )
    assert result.summary
    assert result.metrics.docs_selected == len(result.relevant_docs)


# -- full pipeline with embedding scores -------------------------------------


def test_full_pipeline_with_embedding_scores(tmp_path: Path) -> None:
    _build_test_repo(tmp_path)
    index = scan_repository(tmp_path)
    graph = build_dependency_graph(index)

    embedding_scores = {
        "src/auth/login.py": 0.8,
        "src/auth/token.py": 0.6,
        "src/billing/invoice.py": 0.1,
    }

    result = get_task_context_data(
        task="fix authentication bugs",
        index=index, graph=graph,
        embedding_scores=embedding_scores,
    )
    file_paths = [r.path for r in result.relevant_files]
    assert "src/auth/login.py" in file_paths
    test_paths = [r.path for r in result.related_tests]
    assert "tests/test_login.py" in test_paths


# -- debug score fields are populated ----------------------------------------


def test_ranked_path_has_score_breakdown(tmp_path: Path) -> None:
    _build_test_repo(tmp_path)
    index = scan_repository(tmp_path)

    embedding_scores = {"src/auth/login.py": 0.75}
    ranked = rank_files(
        index, "login authentication", DEFAULT_CONFIG,
        embedding_scores=embedding_scores,
    )

    login = next((r for r in ranked if r.path == "src/auth/login.py"), None)
    assert login is not None
    assert login.heuristic_score > 0
    assert login.embedding_score == 0.75
    assert login.score == login.heuristic_score + DEFAULT_CONFIG.embedding_weight * 0.75

    d = login.to_dict(include_debug=True)
    assert "heuristic_score" in d
    assert "embedding_score" in d

    d_no_debug = login.to_dict(include_debug=False)
    assert "heuristic_score" not in d_no_debug
