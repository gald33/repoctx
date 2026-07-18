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


# ---- `from package import submodule` edges -----------------------------------
#
# `PYTHON_FROM_RE` captured only the package path, so `from pkg import mod`
# produced an edge to `pkg/__init__.py` and none to `pkg/mod.py`. The idiom is
# ubiquitous — repoctx uses it for `from repoctx import reporting` in both
# telemetry.py and mcp_server.py — so `detect_changes` understated blast radius
# and bundles omitted genuinely related files. Silent: no error, just a
# missing edge.


def _graph_for(tmp_path: Path) -> dict:
    return build_dependency_graph(scan_repository(tmp_path)).forward


def _make_pkg(tmp_path: Path) -> None:
    write_file(tmp_path / "pkg" / "__init__.py", "")
    write_file(tmp_path / "pkg" / "mod.py", "VALUE = 1\n")
    write_file(tmp_path / "pkg" / "other.py", "X = 2\n")


def test_from_package_import_submodule_creates_edge(tmp_path: Path) -> None:
    _make_pkg(tmp_path)
    write_file(tmp_path / "app.py", "from pkg import mod\n")

    edges = _graph_for(tmp_path).get("app.py", set())
    assert "pkg/mod.py" in edges, "submodule edge missing"
    assert "pkg/__init__.py" in edges, "package edge should still be present"


def test_from_package_import_multiple_submodules(tmp_path: Path) -> None:
    _make_pkg(tmp_path)
    write_file(tmp_path / "app.py", "from pkg import mod, other as o  # noqa\n")

    edges = _graph_for(tmp_path).get("app.py", set())
    assert {"pkg/mod.py", "pkg/other.py"} <= edges


def test_from_package_import_non_module_makes_no_edge(tmp_path: Path) -> None:
    """A plain symbol must not invent an edge."""
    _make_pkg(tmp_path)
    write_file(tmp_path / "app.py", "from pkg import SOME_CONSTANT\n")

    edges = _graph_for(tmp_path).get("app.py", set())
    assert edges == {"pkg/__init__.py"}


def test_relative_from_import_submodule(tmp_path: Path) -> None:
    _make_pkg(tmp_path)
    write_file(tmp_path / "pkg" / "rel.py", "from . import mod\n")
    write_file(tmp_path / "pkg" / "sub" / "__init__.py", "")
    write_file(tmp_path / "pkg" / "sub" / "up.py", "from .. import other\n")

    forward = _graph_for(tmp_path)
    assert "pkg/mod.py" in forward.get("pkg/rel.py", set())
    assert "pkg/other.py" in forward.get("pkg/sub/up.py", set())


def test_direct_submodule_import_still_resolves(tmp_path: Path) -> None:
    """Regression guard on the form that already worked."""
    _make_pkg(tmp_path)
    write_file(tmp_path / "app.py", "from pkg.mod import VALUE\n")

    assert "pkg/mod.py" in _graph_for(tmp_path).get("app.py", set())


def test_deferred_function_local_from_import_is_seen(tmp_path: Path) -> None:
    """Imports inside a function body count — repoctx defers many itself."""
    _make_pkg(tmp_path)
    write_file(
        tmp_path / "app.py",
        "def go():\n    from pkg import mod\n    return mod.VALUE\n",
    )

    assert "pkg/mod.py" in _graph_for(tmp_path).get("app.py", set())


# ---- Imports past the content-truncation cap ---------------------------------
#
# `scanner` caps `FileRecord.content` at `max_file_bytes` (16 KB). The graph
# read imports from that truncated text, so in a large module every import
# below the cap was invisible — and large files are exactly the central hubs.
# repoctx's own mcp_server.py imports `reporting` at lines 543/1014/1101, all
# past the cap, so its dependency on reporting.py did not exist in the graph.


def test_imports_past_truncation_cap_are_still_seen(tmp_path: Path) -> None:
    from repoctx.config import DEFAULT_CONFIG

    _make_pkg(tmp_path)
    filler = "# " + ("x" * 98) + "\n"
    padding = filler * ((DEFAULT_CONFIG.max_file_bytes // len(filler)) + 20)
    # A deferred import placed deliberately *after* the truncation point.
    write_file(
        tmp_path / "big.py",
        "HEADER = 1\n" + padding + "\ndef late():\n    from pkg import mod\n    return mod\n",
    )

    index = scan_repository(tmp_path)
    record = index.records["big.py"]
    assert len(record.content) == DEFAULT_CONFIG.max_file_bytes, "fixture must truncate"
    assert "from pkg import mod" not in record.content, "import must be past the cap"

    edges = build_dependency_graph(index).forward.get("big.py", set())
    assert "pkg/mod.py" in edges, "import past the truncation cap was dropped"


def test_import_source_only_populated_when_truncated(tmp_path: Path) -> None:
    """Small files carry no extra payload — content already has every import."""
    _make_pkg(tmp_path)
    write_file(tmp_path / "small.py", "from pkg import mod\n")

    index = scan_repository(tmp_path)
    assert index.records["small.py"].import_source == ""
    # ...and the edge still resolves from `content`.
    assert "pkg/mod.py" in build_dependency_graph(index).forward.get("small.py", set())


# ---- Continued (multi-line) from-import clauses -------------------------------
#
# A parenthesized or backslash-continued clause carries names past the matched
# line. 1.10.0 shipped line-scoped and knowingly missed them; `_complete_from_
# clause` now follows the continuation with an explicit bounded scan (never a
# wider regex — that is what caused the 1.9.0 crash).


def test_parenthesized_multiline_from_import(tmp_path: Path) -> None:
    _make_pkg(tmp_path)
    write_file(tmp_path / "app.py", "from pkg import (\n    mod,\n    other,\n)\n")

    edges = _graph_for(tmp_path).get("app.py", set())
    assert {"pkg/mod.py", "pkg/other.py"} <= edges


def test_backslash_continued_from_import(tmp_path: Path) -> None:
    _make_pkg(tmp_path)
    write_file(tmp_path / "app.py", "from pkg import mod, \\\n    other\n")

    edges = _graph_for(tmp_path).get("app.py", set())
    assert {"pkg/mod.py", "pkg/other.py"} <= edges


def test_multiline_clause_with_per_line_comments(tmp_path: Path) -> None:
    _make_pkg(tmp_path)
    write_file(
        tmp_path / "app.py",
        "from pkg import (  # noqa\n    mod,  # keep this\n    other,\n)\n",
    )

    edges = _graph_for(tmp_path).get("app.py", set())
    assert {"pkg/mod.py", "pkg/other.py"} <= edges


def test_continuation_scan_stops_at_closing_paren(tmp_path: Path) -> None:
    """The scan must not run past the statement into unrelated code."""
    _make_pkg(tmp_path)
    write_file(tmp_path / "pkg" / "unrelated.py", "Z = 3\n")
    write_file(
        tmp_path / "app.py",
        "from pkg import (\n    mod,\n)\n\ndef f():\n    unrelated = 1\n    return unrelated\n",
    )

    edges = _graph_for(tmp_path).get("app.py", set())
    assert "pkg/mod.py" in edges
    assert "pkg/unrelated.py" not in edges, "scan ran past the closing paren"


def test_multiline_non_module_names_make_no_edge(tmp_path: Path) -> None:
    _make_pkg(tmp_path)
    write_file(tmp_path / "app.py", "from pkg import (\n    SOME_CONSTANT,\n)\n")

    assert _graph_for(tmp_path).get("app.py", set()) == {"pkg/__init__.py"}


def test_multiline_import_past_truncation_cap(tmp_path: Path) -> None:
    """The hard case: continuation lines must survive import harvesting too.

    `import_source` is built by filtering the untruncated text to import lines;
    a naive filter keeps `from pkg import (` and drops the indented names under
    it, leaving the graph an empty clause.
    """
    from repoctx.config import DEFAULT_CONFIG

    _make_pkg(tmp_path)
    filler = "# " + ("x" * 98) + "\n"
    padding = filler * ((DEFAULT_CONFIG.max_file_bytes // len(filler)) + 20)
    write_file(
        tmp_path / "big.py",
        "HEADER = 1\n"
        + padding
        + "\ndef late():\n    from pkg import (\n        mod,\n        other,\n    )\n    return mod\n",
    )

    index = scan_repository(tmp_path)
    assert "from pkg import (" not in index.records["big.py"].content, "must be past cap"

    edges = build_dependency_graph(index).forward.get("big.py", set())
    assert {"pkg/mod.py", "pkg/other.py"} <= edges


# ---- ast-based import extraction ---------------------------------------------
#
# Import extraction parses with `ast` and only falls back to the regex when the
# source won't parse. Every import bug this module shipped was a regex failing
# to model Python's grammar; these cover the forms that motivated the switch.


def test_import_inside_docstring_is_not_an_edge(tmp_path: Path) -> None:
    """The regex saw example imports in docstrings as real ones."""
    _make_pkg(tmp_path)
    write_file(
        tmp_path / "app.py",
        '"""Example usage:\n\nimport pkg.mod\n"""\n\nVALUE = 1\n',
    )

    assert _graph_for(tmp_path).get("app.py", set()) == set()


def test_semicolon_separated_imports(tmp_path: Path) -> None:
    _make_pkg(tmp_path)
    write_file(tmp_path / "app.py", "import pkg.mod; import pkg.other\n")

    edges = _graph_for(tmp_path).get("app.py", set())
    assert {"pkg/mod.py", "pkg/other.py"} <= edges


def test_conditional_and_guarded_imports(tmp_path: Path) -> None:
    _make_pkg(tmp_path)
    write_file(
        tmp_path / "type_checking.py",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from pkg import other\n",
    )
    write_file(
        tmp_path / "guarded.py",
        "try:\n    from pkg import mod\nexcept ImportError:\n    mod = None\n",
    )

    forward = _graph_for(tmp_path)
    assert "pkg/other.py" in forward.get("type_checking.py", set())
    assert "pkg/mod.py" in forward.get("guarded.py", set())


def test_star_import_resolves_package_only(tmp_path: Path) -> None:
    _make_pkg(tmp_path)
    write_file(tmp_path / "app.py", "from pkg import *\n")

    assert _graph_for(tmp_path).get("app.py", set()) == {"pkg/__init__.py"}


def test_unparseable_source_falls_back_to_regex(tmp_path: Path) -> None:
    """A syntax error must degrade, not drop the file's imports entirely."""
    _make_pkg(tmp_path)
    write_file(
        tmp_path / "broken.py",
        "from pkg import mod\n\ndef oops(:\n    this is not python\n",
    )

    # ast.parse raises; the regex fallback still finds the import.
    assert "pkg/mod.py" in _graph_for(tmp_path).get("broken.py", set())


def test_ast_path_handles_truncated_files_without_falling_back(
    tmp_path: Path, monkeypatch
) -> None:
    """`import_source` must parse: it is dedented for exactly this reason.

    A function-local import carried over with its original indentation is an
    IndentationError at module level, which would silently drop every
    truncated (i.e. large, central) file back to the regex fallback.
    """
    import repoctx.graph as graph_module
    from repoctx.config import DEFAULT_CONFIG

    _make_pkg(tmp_path)
    filler = "# " + ("x" * 98) + "\n"
    padding = filler * ((DEFAULT_CONFIG.max_file_bytes // len(filler)) + 20)
    write_file(
        tmp_path / "big.py",
        "HEADER = 1\n" + padding + "\ndef late():\n    from pkg import mod\n    return mod\n",
    )

    fallbacks = []
    original = graph_module._extract_python_dependencies_regex

    def spy(*args, **kwargs):
        fallbacks.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(graph_module, "_extract_python_dependencies_regex", spy)

    index = scan_repository(tmp_path)
    edges = build_dependency_graph(index).forward.get("big.py", set())

    assert "pkg/mod.py" in edges
    assert "big.py" not in fallbacks, "truncated file fell back to regex"
