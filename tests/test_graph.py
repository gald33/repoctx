from pathlib import Path

from repoctx.graph import build_dependency_graph, expand_graph_neighbors
from repoctx.scanner import scan_repository


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_graph_neighbor_expansion_finds_forward_and_reverse_links(tmp_path: Path) -> None:
    write_file(
        tmp_path / "src" / "service.py",
        "from .helpers import helper\n\ndef run():\n    return helper()\n",
    )
    write_file(
        tmp_path / "src" / "helpers.py",
        "def helper():\n    return 'ok'\n",
    )
    write_file(
        tmp_path / "tests" / "test_service.py",
        "from src.service import run\n",
    )

    index = scan_repository(tmp_path)
    graph = build_dependency_graph(index)
    neighbors = expand_graph_neighbors(
        index=index,
        graph=graph,
        seed_paths=["src/service.py"],
    )
    neighbor_paths = {item.path for item in neighbors}

    assert "src/helpers.py" in neighbor_paths
    assert "tests/test_service.py" in neighbor_paths
