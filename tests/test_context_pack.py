from repoctx.context_pack import render_context_markdown
from repoctx.models import ContextResponse, RankedPath


def test_render_context_markdown_includes_sections() -> None:
    response = ContextResponse(
        summary="The task is likely in webhook retry code.",
        relevant_docs=[RankedPath(path="AGENTS.md", reason="Root agent guidance", score=8.0)],
        relevant_files=[
            RankedPath(
                path="src/webhook/retry_policy.py",
                reason="Task tokens match retry logic",
                score=12.0,
                snippet="def compute_retry_delay():",
            )
        ],
        related_tests=[
            RankedPath(
                path="tests/test_retry_policy.py",
                reason="Matching stem and test path",
                score=6.0,
            )
        ],
        graph_neighbors=[
            RankedPath(
                path="src/webhook/delivery.py",
                reason="Imports retry policy",
                score=5.0,
            )
        ],
        context_markdown="",
    )

    markdown = render_context_markdown(response)

    assert "## Summary" in markdown
    assert "`AGENTS.md`" in markdown
    assert "`src/webhook/retry_policy.py`" in markdown
    assert "## Graph Neighbors" in markdown
    assert "Snippet: def compute_retry_delay():" in markdown
