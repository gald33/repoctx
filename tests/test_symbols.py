"""Tests for repoctx.symbols.extract_symbols."""

from pathlib import Path

import pytest

from repoctx.models import FileRecord
from repoctx.symbols import Symbol, extract_symbols


def _record(content: str, ext: str = ".py", path: str = "x") -> FileRecord:
    return FileRecord(
        path=f"{path}{ext}",
        absolute_path=Path(f"/tmp/{path}{ext}"),
        extension=ext,
        kind="code",
        content=content,
    )


# ---------- Python ------------------------------------------------------------


def test_python_top_level_functions():
    src = "def foo():\n    return 1\n\ndef bar():\n    return 2\n"
    symbols = extract_symbols(_record(src))
    names = [(s.qualified_name, s.kind) for s in symbols]
    assert names == [("foo", "function"), ("bar", "function")]
    assert symbols[0].start_line == 1
    assert symbols[0].end_line == 2
    assert symbols[1].start_line == 4


def test_python_async_function():
    src = "async def fetch():\n    return await x()\n"
    [sym] = extract_symbols(_record(src))
    assert sym.kind == "async_function"
    assert sym.qualified_name == "fetch"


def test_python_class_with_methods():
    src = (
        "class Foo:\n"
        "    def bar(self):\n"
        "        return 1\n"
        "    async def baz(self):\n"
        "        return 2\n"
    )
    symbols = extract_symbols(_record(src))
    names = [(s.qualified_name, s.kind) for s in symbols]
    # Class spans first because its start_line < method start_lines.
    assert names == [
        ("Foo", "class"),
        ("Foo.bar", "method"),
        ("Foo.baz", "async_method"),
    ]


def test_python_nested_class():
    src = (
        "class Outer:\n"
        "    class Inner:\n"
        "        def m(self):\n"
        "            return 1\n"
    )
    symbols = extract_symbols(_record(src))
    qnames = [s.qualified_name for s in symbols]
    assert qnames == ["Outer", "Outer.Inner", "Outer.Inner.m"]


def test_python_skips_nested_function():
    src = (
        "def outer():\n"
        "    def inner():\n"
        "        return 1\n"
        "    return inner\n"
    )
    symbols = extract_symbols(_record(src))
    assert [s.qualified_name for s in symbols] == ["outer"]


def test_python_syntax_error_returns_empty():
    assert extract_symbols(_record("def (:\n")) == []


def test_empty_content_returns_empty():
    assert extract_symbols(_record("")) == []


def test_unknown_extension_returns_empty():
    assert extract_symbols(_record("anything", ext=".xyz")) == []


def test_symbols_sorted_by_start_line():
    src = (
        "class A:\n"
        "    def m1(self):\n"
        "        pass\n"
        "    def m2(self):\n"
        "        pass\n"
        "\n"
        "def top():\n"
        "    pass\n"
    )
    symbols = extract_symbols(_record(src))
    starts = [s.start_line for s in symbols]
    assert starts == sorted(starts)


# ---------- tree-sitter (skipped if not installed) ----------------------------


def _ts_available() -> bool:
    try:
        import tree_sitter_language_pack  # noqa: F401
        return True
    except Exception:
        return False


pytestmark_ts = pytest.mark.skipif(
    not _ts_available(), reason="tree-sitter-language-pack not installed"
)


@pytestmark_ts
def test_javascript_function_and_class():
    src = (
        "function foo() { return 1; }\n"
        "class Bar {\n"
        "  baz() { return 2; }\n"
        "}\n"
    )
    symbols = extract_symbols(_record(src, ext=".js"))
    names = [(s.qualified_name, s.kind) for s in symbols]
    assert ("foo", "function") in names
    assert ("Bar", "class") in names
    assert ("Bar.baz", "method") in names


@pytestmark_ts
def test_typescript_interface_and_method():
    src = (
        "interface Greeter { hello(): string; }\n"
        "class Foo {\n"
        "  greet(name: string): string { return name; }\n"
        "}\n"
    )
    symbols = extract_symbols(_record(src, ext=".ts"))
    names = {s.qualified_name for s in symbols}
    assert "Greeter" in names
    assert "Foo" in names
    assert "Foo.greet" in names


@pytestmark_ts
def test_tsx_arrow_component():
    src = (
        "const Button = (props: Props) => {\n"
        "  return <button />;\n"
        "};\n"
    )
    symbols = extract_symbols(_record(src, ext=".tsx"))
    names = [s.qualified_name for s in symbols]
    assert "Button" in names


@pytestmark_ts
def test_go_function_and_method():
    src = (
        "package main\n"
        "func Foo() int { return 1 }\n"
        "type Bar struct{}\n"
        "func (b *Bar) Baz() int { return 2 }\n"
        "func (b Bar) Qux() int { return 3 }\n"
    )
    symbols = extract_symbols(_record(src, ext=".go"))
    names = {s.qualified_name for s in symbols}
    assert "Foo" in names
    assert "Bar" in names
    # Receiver type prefixes the method name regardless of pointer vs value.
    assert "Bar.Baz" in names
    assert "Bar.Qux" in names
    # Methods get kind="method" once they're receiver-qualified.
    by_name = {s.qualified_name: s for s in symbols}
    assert by_name["Bar.Baz"].kind == "method"


@pytestmark_ts
def test_rust_function_and_struct():
    src = (
        "struct Foo { x: i32 }\n"
        "fn bar() -> i32 { 1 }\n"
    )
    symbols = extract_symbols(_record(src, ext=".rs"))
    names = {s.qualified_name for s in symbols}
    assert "Foo" in names
    assert "bar" in names


@pytestmark_ts
def test_rust_impl_block_qualifies_methods():
    # Methods inside an impl block fall lexically inside the impl_item span,
    # so the class_stack mechanism should qualify them as Foo.method.
    src = (
        "struct Foo { x: i32 }\n"
        "impl Foo {\n"
        "    fn bar(&self) -> i32 { self.x }\n"
        "    fn baz() -> i32 { 1 }\n"
        "}\n"
        "fn top() -> i32 { 2 }\n"
    )
    symbols = extract_symbols(_record(src, ext=".rs"))
    names = {s.qualified_name for s in symbols}
    assert "Foo.bar" in names
    assert "Foo.baz" in names
    # Top-level function stays unqualified.
    assert "top" in names


def test_symbol_dataclass_is_frozen():
    s = Symbol("foo", "function", 1, 2)
    with pytest.raises(Exception):
        s.start_line = 5  # type: ignore[misc]
