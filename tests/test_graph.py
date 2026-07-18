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


# ---- Import-regex regression -------------------------------------------------
#
# `PYTHON_IMPORT_RE` used `[.\w\s,]+`, whose `\s` matches newlines. One
# `import os` therefore swallowed every following line until a character
# outside the class, capturing whole `from x import a, b,` blocks. That
# produced false dependency edges and — when the run ended on a trailing
# comma — an empty split segment that raised IndexError, taking down every
# protocol op on the affected repo. Shape lifted from pygments, the real
# trigger found in the wild.


PYGMENTS_SHAPE = (
    "import os\n"
    "import sys\n"
    "import shutil\n"
    "import argparse\n"
    "from textwrap import dedent\n"
    "\n"
    "from pkg.util import ClassNotFound, OptionError, docstring_headline, \\\n"
    "    make_analysator\n"
)


def test_import_regex_does_not_crash_on_multiline_block(tmp_path: Path) -> None:
    """The exact shape that raised IndexError in the wild must scan cleanly."""
    write_file(tmp_path / "pkg" / "__init__.py", "")
    write_file(tmp_path / "pkg" / "util.py", "def make_analysator():\n    return None\n")
    write_file(tmp_path / "pkg" / "cmdline.py", PYGMENTS_SHAPE)

    index = scan_repository(tmp_path)
    graph = build_dependency_graph(index)  # must not raise
    assert isinstance(graph.forward, dict)


def test_import_regex_does_not_capture_across_lines(tmp_path: Path) -> None:
    """`import config` on one line must not absorb the next line's symbols.

    Regression for the false-edge half of the bug: symbols from a following
    `from ... import a, b` line were looked up as module names, so a symbol
    sharing a name with a real module produced a bogus dependency edge.
    """
    write_file(tmp_path / "pkg" / "__init__.py", "")
    write_file(tmp_path / "pkg" / "settings.py", "VALUE = 1\n")
    write_file(tmp_path / "pkg" / "unrelated.py", "OTHER = 2\n")
    # `import os` is followed by a from-import naming `unrelated` as a symbol.
    write_file(
        tmp_path / "pkg" / "app.py",
        "import os\nfrom pkg.settings import VALUE, unrelated\n",
    )

    index = scan_repository(tmp_path)
    graph = build_dependency_graph(index)
    edges = graph.forward.get("pkg/app.py", set())
    assert "pkg/unrelated.py" not in edges, (
        "symbol from a from-import leaked into the import regex as a module name"
    )


def test_import_regex_still_resolves_normal_imports(tmp_path: Path) -> None:
    """The narrowed regex must not regress the cases that already worked."""
    write_file(tmp_path / "pkg" / "__init__.py", "")
    write_file(tmp_path / "pkg" / "alpha.py", "A = 1\n")
    write_file(tmp_path / "pkg" / "beta.py", "B = 2\n")
    write_file(tmp_path / "pkg" / "main.py", "import pkg.alpha, pkg.beta as b\n")

    index = scan_repository(tmp_path)
    graph = build_dependency_graph(index)
    edges = graph.forward.get("pkg/main.py", set())
    assert "pkg/alpha.py" in edges
    assert "pkg/beta.py" in edges, "`import x as y` should resolve the module, not the alias"


def test_import_with_trailing_comma_is_skipped_not_crashed(tmp_path: Path) -> None:
    write_file(tmp_path / "pkg" / "__init__.py", "")
    write_file(tmp_path / "pkg" / "alpha.py", "A = 1\n")
    write_file(tmp_path / "pkg" / "main.py", "import pkg.alpha,\n")

    index = scan_repository(tmp_path)
    graph = build_dependency_graph(index)  # must not raise
    assert "pkg/alpha.py" in graph.forward.get("pkg/main.py", set())
