"""Symbol extraction for code files.

Returns line-range spans for top-level functions, methods, and classes so the
chunker can prefer those boundaries when packing windows. Python uses the
stdlib ``ast`` module; other languages go through ``tree-sitter-languages``.
Languages without a registered handler return an empty list, which the
chunker treats as a signal to fall back to pure sliding-window splitting.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass
from typing import Callable

from repoctx.models import FileRecord

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Symbol:
    qualified_name: str
    kind: str  # "function" | "async_function" | "method" | "async_method" | "class"
    start_line: int  # 1-indexed inclusive
    end_line: int  # 1-indexed inclusive


def extract_symbols(record: FileRecord) -> list[Symbol]:
    """Return symbol spans for *record*; empty list for unsupported languages."""
    if not record.content:
        return []
    ext = record.extension.lower()
    if ext == ".py":
        return _extract_python(record.content)
    handler = _TREE_SITTER_HANDLERS.get(ext)
    if handler is None:
        return []
    try:
        return handler(record.content)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Symbol extraction failed for %s: %s", record.path, exc)
        return []


# ---------- Python (ast) ------------------------------------------------------


def _extract_python(source: str) -> list[Symbol]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    out: list[Symbol] = []

    def visit(node: ast.AST, prefix: str, in_class: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qname = f"{prefix}{child.name}" if prefix else child.name
                if in_class:
                    kind = "async_method" if isinstance(child, ast.AsyncFunctionDef) else "method"
                else:
                    kind = "async_function" if isinstance(child, ast.AsyncFunctionDef) else "function"
                end = child.end_lineno or child.lineno
                out.append(Symbol(qname, kind, child.lineno, end))
                # Don't recurse into function bodies — nested defs are rare and
                # treating them as their own chunks tends to fragment usefully-
                # cohesive logic.
            elif isinstance(child, ast.ClassDef):
                qname = f"{prefix}{child.name}" if prefix else child.name
                end = child.end_lineno or child.lineno
                out.append(Symbol(qname, "class", child.lineno, end))
                visit(child, qname + ".", in_class=True)

    visit(tree, prefix="", in_class=False)
    out.sort(key=lambda s: (s.start_line, s.end_line))
    return out


# ---------- tree-sitter -------------------------------------------------------

# Loaded lazily so importing this module is cheap when no non-Python files are
# touched. The tree-sitter deps are optional (see pyproject.toml [embeddings]):
# missing them is fine — we just return [] and the chunker falls back to pure
# sliding-window splitting.

_PARSERS: dict[str, object] = {}
_QUERIES: dict[str, object] = {}


def _parser_for(language: str):
    if language not in _PARSERS:
        from tree_sitter_language_pack import get_parser  # type: ignore[import-not-found]

        _PARSERS[language] = get_parser(language)
    return _PARSERS[language]


def _query_for(language: str, query_src: str):
    key = f"{language}::{hash(query_src)}"
    if key not in _QUERIES:
        from tree_sitter import Query  # type: ignore[import-not-found]
        from tree_sitter_language_pack import get_language  # type: ignore[import-not-found]

        _QUERIES[key] = Query(get_language(language), query_src)
    return _QUERIES[key]


def _ts_extract(
    source: str,
    language: str,
    query_src: str,
    name_capture: str = "name",
    body_capture: str = "def",
    receiver_capture: str | None = None,
    kind_for: Callable[[str], str] | None = None,
) -> list[Symbol]:
    """Run *query_src* against *source* and assemble Symbols.

    The query must capture each definition with two names: the def node
    (``@def`` by default) and the identifier holding its name (``@name``).
    ``kind_for`` maps the def node's grammar type to our Symbol.kind string.

    When *receiver_capture* is set and present in a match (e.g. Go method
    receivers), the receiver text prefixes the name as ``Receiver.name`` and
    the kind is upgraded from function to method.
    """
    from tree_sitter import QueryCursor  # type: ignore[import-not-found]

    parser = _parser_for(language)
    tree = parser.parse(source.encode("utf-8"))
    query = _query_for(language, query_src)
    matches = QueryCursor(query).matches(tree.root_node)

    out: list[Symbol] = []
    class_stack: list[tuple[str, int]] = []  # (qname, end_line)

    flat: list[tuple[object, object, object | None, str]] = []
    source_bytes = source.encode("utf-8")
    for _pattern_idx, captures in matches:
        def_node = captures.get(body_capture)
        name_node = captures.get(name_capture)
        receiver_node = captures.get(receiver_capture) if receiver_capture else None
        if def_node is None or name_node is None:
            continue
        if isinstance(def_node, list):
            def_node = def_node[0]
        if isinstance(name_node, list):
            name_node = name_node[0]
        if isinstance(receiver_node, list):
            receiver_node = receiver_node[0] if receiver_node else None
        kind = kind_for(def_node.type) if kind_for else def_node.type
        flat.append((def_node, name_node, receiver_node, kind))
    flat.sort(key=lambda t: (t[0].start_point[0], t[0].end_point[0]))

    for def_node, name_node, receiver_node, kind in flat:
        start = def_node.start_point[0] + 1
        end = def_node.end_point[0] + 1
        while class_stack and start > class_stack[-1][1]:
            class_stack.pop()
        name = source_bytes[name_node.start_byte : name_node.end_byte].decode(
            "utf-8", errors="replace"
        )
        if receiver_node is not None:
            receiver = source_bytes[receiver_node.start_byte : receiver_node.end_byte].decode(
                "utf-8", errors="replace"
            )
            qname = f"{receiver}.{name}"
            if kind == "function":
                kind = "method"
            elif kind == "async_function":
                kind = "async_method"
        elif class_stack:
            qname = f"{class_stack[-1][0]}.{name}"
            if kind == "function":
                kind = "method"
            elif kind == "async_function":
                kind = "async_method"
        else:
            qname = name
        out.append(Symbol(qname, kind, start, end))
        if kind == "class":
            class_stack.append((qname, end))

    out.sort(key=lambda s: (s.start_line, s.end_line))
    return out


# JavaScript / TypeScript queries. JS classes use (identifier) for the name,
# TS/TSX classes use (type_identifier) — and a single impossible pattern
# rejects the whole compiled query, so we keep them separate.
_JS_QUERY = r"""
(function_declaration name: (identifier) @name) @def
(class_declaration name: (identifier) @name) @def
(method_definition name: (property_identifier) @name) @def
(variable_declarator
  name: (identifier) @name
  value: [(arrow_function) (function_expression)]) @def
"""

_TS_QUERY = r"""
(function_declaration name: (identifier) @name) @def
(class_declaration name: (type_identifier) @name) @def
(method_definition name: (property_identifier) @name) @def
(variable_declarator
  name: (identifier) @name
  value: [(arrow_function) (function_expression)]) @def
(interface_declaration name: (type_identifier) @name) @def
(type_alias_declaration name: (type_identifier) @name) @def
"""


def _kind_js(node_type: str) -> str:
    if node_type in {"class_declaration", "interface_declaration"}:
        return "class"
    if node_type == "type_alias_declaration":
        return "type"
    if node_type == "method_definition":
        return "method"
    return "function"


def _extract_js(source: str) -> list[Symbol]:
    return _ts_extract(source, "javascript", _JS_QUERY, kind_for=_kind_js)


def _extract_ts(source: str) -> list[Symbol]:
    return _ts_extract(source, "typescript", _TS_QUERY, kind_for=_kind_js)


def _extract_tsx(source: str) -> list[Symbol]:
    return _ts_extract(source, "tsx", _TS_QUERY, kind_for=_kind_js)


# Go. Method receivers are sibling nodes to the method body, not lexical
# parents, so we capture them explicitly and prefix the name.
_GO_QUERY = r"""
(function_declaration name: (identifier) @name) @def
(method_declaration
  receiver: (parameter_list
    (parameter_declaration
      type: [(pointer_type (type_identifier) @receiver)
             (type_identifier) @receiver]))
  name: (field_identifier) @name) @def
(type_declaration (type_spec name: (type_identifier) @name)) @def
"""


def _kind_go(node_type: str) -> str:
    if node_type == "type_declaration":
        return "class"
    if node_type == "method_declaration":
        return "method"
    return "function"


def _extract_go(source: str) -> list[Symbol]:
    return _ts_extract(
        source,
        "go",
        _GO_QUERY,
        receiver_capture="receiver",
        kind_for=_kind_go,
    )


# Rust.
_RUST_QUERY = r"""
(function_item name: (identifier) @name) @def
(struct_item name: (type_identifier) @name) @def
(enum_item name: (type_identifier) @name) @def
(trait_item name: (type_identifier) @name) @def
(impl_item type: (type_identifier) @name) @def
"""


def _kind_rust(node_type: str) -> str:
    if node_type in {"struct_item", "enum_item", "trait_item", "impl_item"}:
        return "class"
    return "function"


def _extract_rust(source: str) -> list[Symbol]:
    return _ts_extract(source, "rust", _RUST_QUERY, kind_for=_kind_rust)


# Java.
_JAVA_QUERY = r"""
(class_declaration name: (identifier) @name) @def
(interface_declaration name: (identifier) @name) @def
(method_declaration name: (identifier) @name) @def
(constructor_declaration name: (identifier) @name) @def
"""


def _kind_java(node_type: str) -> str:
    if node_type in {"class_declaration", "interface_declaration"}:
        return "class"
    return "method"


def _extract_java(source: str) -> list[Symbol]:
    return _ts_extract(source, "java", _JAVA_QUERY, kind_for=_kind_java)


_TREE_SITTER_HANDLERS: dict[str, Callable[[str], list[Symbol]]] = {
    ".js": _extract_js,
    ".jsx": _extract_js,
    ".mjs": _extract_js,
    ".cjs": _extract_js,
    ".ts": _extract_ts,
    ".tsx": _extract_tsx,
    ".go": _extract_go,
    ".rs": _extract_rust,
    ".java": _extract_java,
}
