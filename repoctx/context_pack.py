from repoctx.models import ContextResponse, RankedPath


def render_context_markdown(response: ContextResponse) -> str:
    sections = [
        "## Summary",
        response.summary,
        "",
        _render_section("Relevant Docs", response.relevant_docs),
        "",
        _render_section("Relevant Files", response.relevant_files),
        "",
        _render_section("Related Tests", response.related_tests),
        "",
        _render_section("Graph Neighbors", response.graph_neighbors),
    ]
    return "\n".join(section for section in sections if section is not None).strip()


def _render_section(title: str, items: list[RankedPath]) -> str:
    lines = [f"## {title}"]
    if not items:
        lines.append("- None")
        return "\n".join(lines)

    for item in items:
        lines.append(f"- `{item.path}`: {item.reason}")
        if item.snippet:
            lines.append(f"  Snippet: {item.snippet}")
    return "\n".join(lines)
