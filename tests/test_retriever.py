from pathlib import Path

from repoctx.graph import build_dependency_graph
from repoctx.retriever import get_task_context_data
from repoctx.scanner import scan_repository


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_task_retrieval_prioritizes_matching_code_and_tests(tmp_path: Path) -> None:
    write_file(tmp_path / "AGENTS.md", "# Repo guidance\n")
    write_file(tmp_path / "docs" / "ROADMAP.md", "Company roadmap and staffing plan.\n")
    write_file(
        tmp_path / "docs" / "WEBHOOKS.md",
        "Webhook delivery retries should use retry jitter.\n",
    )
    write_file(
        tmp_path / "src" / "webhook" / "retry_policy.py",
        "def compute_retry_delay():\n    return 1\n",
    )
    write_file(
        tmp_path / "src" / "webhook" / "delivery.py",
        "from .retry_policy import compute_retry_delay\n",
    )
    write_file(
        tmp_path / "src" / "email" / "sender.py",
        "def send_email():\n    return True\n",
    )
    write_file(
        tmp_path / "tests" / "test_retry_policy.py",
        "from src.webhook.retry_policy import compute_retry_delay\n",
    )

    index = scan_repository(tmp_path)
    graph = build_dependency_graph(index)
    result = get_task_context_data(
        task="add retry jitter to webhook delivery",
        index=index,
        graph=graph,
    )

    relevant_file_paths = [item.path for item in result.relevant_files]
    relevant_doc_paths = [item.path for item in result.relevant_docs]
    related_test_paths = [item.path for item in result.related_tests]

    assert relevant_file_paths[0] in {
        "src/webhook/retry_policy.py",
        "src/webhook/delivery.py",
    }
    assert "AGENTS.md" in relevant_doc_paths
    assert "docs/WEBHOOKS.md" in relevant_doc_paths
    assert "docs/ROADMAP.md" not in relevant_doc_paths
    assert "tests/test_retry_policy.py" in related_test_paths
    assert result.summary
    assert result.metrics.files_selected == len(result.relevant_files)
    assert result.metrics.docs_selected == len(result.relevant_docs)
    assert result.metrics.tests_selected == len(result.related_tests)
    assert result.metrics.neighbors_selected == len(result.graph_neighbors)
    assert result.metrics.output_bytes > 0


def test_task_retrieval_handles_capitalized_tokens_and_rule_docs(tmp_path: Path) -> None:
    write_file(tmp_path / ".cursor" / "rules" / "vercel-env.mdc", "Sync local env with Vercel before testing.\n")
    write_file(tmp_path / "docs" / "ROADMAP.md", "Hiring plan and roadmap.\n")
    write_file(
        tmp_path / "tooling" / "vercel_env.py",
        "def sync_local_env():\n    return 'Vercel env sync'\n",
    )

    index = scan_repository(tmp_path)
    graph = build_dependency_graph(index)
    result = get_task_context_data(
        task="Sync local env with Vercel",
        index=index,
        graph=graph,
    )

    relevant_doc_paths = [item.path for item in result.relevant_docs]
    relevant_file_paths = [item.path for item in result.relevant_files]

    assert ".cursor/rules/vercel-env.mdc" in relevant_doc_paths
    assert "tooling/vercel_env.py" in relevant_file_paths
    assert "docs/ROADMAP.md" not in relevant_doc_paths
