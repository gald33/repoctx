import re
from collections import defaultdict
from pathlib import Path, PurePosixPath

from repoctx.config import DEFAULT_CONFIG, RepoCtxConfig
from repoctx.models import DependencyGraph, RankedPath, RepositoryIndex

# Group 1 is the package path, group 2 the imported-names clause. Capturing the
# names matters: ``from pkg import mod`` imports a *submodule*, and resolving
# only group 1 reaches ``pkg/__init__.py`` while missing ``pkg/mod.py`` — the
# real dependency. Line-scoped ([ \t], `[^\n]*`) on purpose; a `\s`-based class
# here is what let the sibling import regex swallow whole blocks (see 1.9.0).
# Clauses continued across lines (parenthesized or backslash) are followed by
# ``_complete_from_clause`` — an explicit bounded scan rather than a wider regex.
PYTHON_FROM_RE = re.compile(
    r"^[ \t]*from[ \t]+([.\w]+)[ \t]+import[ \t]+([^\n]*)", re.MULTILINE
)
# Horizontal whitespace only. A bare ``\s`` in the character class matches
# newlines, so one ``import os`` greedily swallowed every following line until
# a character outside ``[.\w\s,]`` — capturing whole ``from x import a, b,``
# blocks. That produced bogus module names (a symbol like ``config`` resolving
# against a real ``config.py``, creating false dependency edges) and, when the
# captured run ended on a trailing comma, an empty segment that crashed the
# split below with IndexError — taking down every protocol op on that repo.
PYTHON_IMPORT_RE = re.compile(r"^[ \t]*import[ \t]+([.\w][.\w,\t ]*)", re.MULTILINE)
TS_IMPORT_RE = re.compile(
    r"""(?:import|export)\s+(?:[^'"]+?\s+from\s+)?['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\)"""
)


def build_dependency_graph(index: RepositoryIndex) -> DependencyGraph:
    forward: dict[str, set[str]] = defaultdict(set)
    reverse: dict[str, set[str]] = defaultdict(set)
    python_modules = _build_python_module_map(index)

    for record in index.code_files + index.test_files:
        for dependency in _extract_dependencies(
            record.path,
            record.content,
            index,
            python_modules,
            import_source=record.import_source,
        ):
            if dependency == record.path:
                continue
            forward[record.path].add(dependency)
            reverse[dependency].add(record.path)

    return DependencyGraph(forward=dict(forward), reverse=dict(reverse))


def expand_graph_neighbors(
    index: RepositoryIndex,
    graph: DependencyGraph,
    seed_paths: list[str],
    config: RepoCtxConfig = DEFAULT_CONFIG,
) -> list[RankedPath]:
    ranked: dict[str, RankedPath] = {}

    for seed_path in seed_paths:
        for neighbor in sorted(graph.forward.get(seed_path, set())):
            _upsert_ranked_path(
                ranked,
                path=neighbor,
                score=4.0,
                reason=f"Imported by `{seed_path}`",
            )
        for neighbor in sorted(graph.reverse.get(seed_path, set())):
            _upsert_ranked_path(
                ranked,
                path=neighbor,
                score=4.5,
                reason=f"References `{seed_path}`",
            )

    for seed_path in seed_paths:
        ranked.pop(seed_path, None)

    results = sorted(ranked.values(), key=lambda item: (-item.score, item.path))
    return results[: config.max_neighbors]


def _upsert_ranked_path(
    ranked: dict[str, RankedPath],
    path: str,
    score: float,
    reason: str,
) -> None:
    existing = ranked.get(path)
    if existing is None or score > existing.score:
        ranked[path] = RankedPath(path=path, reason=reason, score=score)


def _extract_dependencies(
    path: str,
    content: str,
    index: RepositoryIndex,
    python_modules: dict[str, str],
    import_source: str = "",
) -> set[str]:
    if path.endswith(".py"):
        # `content` is truncated at `max_file_bytes`; `import_source` (set only
        # when that truncation actually dropped something) carries the import
        # lines from the whole file so late imports still register.
        return _extract_python_dependencies(
            path, import_source or content, python_modules
        )
    if path.endswith((".ts", ".tsx", ".js", ".jsx")):
        return _extract_ts_dependencies(path, content, index)
    return set()


def _extract_python_dependencies(
    path: str,
    content: str,
    python_modules: dict[str, str],
) -> set[str]:
    dependencies: set[str] = set()
    module_parts = _python_module_parts(path)
    is_package = path.endswith("__init__.py")

    for match in PYTHON_FROM_RE.finditer(content):
        package_path = match.group(1)
        resolved = _resolve_python_import(
            import_path=package_path,
            current_parts=module_parts,
            is_package=is_package,
            module_map=python_modules,
        )
        if resolved:
            dependencies.add(resolved)

        # ``from pkg import mod`` depends on ``pkg/mod.py``, not just
        # ``pkg/__init__.py``. Resolve each imported name as a candidate
        # submodule. A name that isn't a module (``from pkg import CONSTANT``)
        # simply doesn't resolve, so this cannot invent edges.
        for name in _from_import_names(_complete_from_clause(content, match)):
            submodule_path = (
                package_path + name
                if package_path.endswith(".")  # ``from . import mod`` -> ``.mod``
                else f"{package_path}.{name}"
            )
            submodule = _resolve_python_import(
                import_path=submodule_path,
                current_parts=module_parts,
                is_package=is_package,
                module_map=python_modules,
            )
            if submodule:
                dependencies.add(submodule)

    for match in PYTHON_IMPORT_RE.finditer(content):
        for item in match.group(1).split(","):
            # ``import a as b`` -> take the module, not the alias. An empty
            # segment (trailing comma, stray separator) yields no parts and is
            # skipped rather than indexed into.
            parts = item.strip().split()
            if not parts:
                continue
            resolved = python_modules.get(parts[0])
            if resolved:
                dependencies.add(resolved)

    return dependencies


# A parenthesized or backslash-continued import clause spans lines. The scan
# below follows it explicitly and with a hard bound, rather than widening the
# regex — a `\s`-based class spanning lines is exactly what let the sibling
# import regex swallow whole blocks and crash every protocol op (see 1.9.0).
_MAX_CONTINUATION_LINES = 50


def _complete_from_clause(content: str, match: re.Match[str]) -> str:
    """Return the full imported-names clause, following line continuations.

    ``from x import (\\n a,\\n b,\\n)`` and ``from x import a, \\`` both carry
    names past the matched line. Returns the single-line clause untouched when
    nothing is open, so the common case costs one paren count.
    """
    clause = match.group(2)
    depth = clause.count("(") - clause.count(")")
    if depth <= 0 and not clause.rstrip().endswith("\\"):
        return clause

    parts = [clause]
    # The match ends *before* the newline, so drop it — otherwise splitlines()
    # yields a leading "" that looks like an unbroken, unclosed line and ends
    # a backslash continuation one line early.
    rest = content[match.end():]
    if rest.startswith("\n"):
        rest = rest[1:]
    for offset, line in enumerate(rest.splitlines()):
        if offset >= _MAX_CONTINUATION_LINES:
            break
        parts.append(line)
        depth += line.count("(") - line.count(")")
        if depth <= 0 and not line.rstrip().endswith("\\"):
            break
    return "\n".join(parts)


def _from_import_names(clause: str) -> list[str]:
    """Names bound by the ``import`` clause of a ``from X import ...``.

    ``a, b as c`` -> ``["a", "b"]`` (the alias is never the module). Comments
    are stripped per line (the clause may span lines); parens, backslashes,
    ``*``, and anything that isn't a bare identifier are dropped, so junk can
    never reach the resolver.
    """
    stripped = " ".join(line.split("#", 1)[0] for line in clause.splitlines())
    text = stripped.replace("(", " ").replace(")", " ").replace("\\", " ")
    names: list[str] = []
    for item in text.split(","):
        parts = item.strip().split()
        if not parts:
            continue
        name = parts[0]
        if name.isidentifier():
            names.append(name)
    return names


def _resolve_python_import(
    import_path: str,
    current_parts: list[str],
    is_package: bool,
    module_map: dict[str, str],
) -> str | None:
    leading_dots = len(import_path) - len(import_path.lstrip("."))
    stripped = import_path.lstrip(".")

    if leading_dots:
        base = list(current_parts if is_package else current_parts[:-1])
        if leading_dots > 1:
            base = base[: max(0, len(base) - (leading_dots - 1))]
        parts = base + ([segment for segment in stripped.split(".") if segment] if stripped else [])
        module_name = ".".join(parts)
    else:
        module_name = stripped

    return module_map.get(module_name)


def _build_python_module_map(index: RepositoryIndex) -> dict[str, str]:
    modules: dict[str, str] = {}
    for record in index.records.values():
        if not record.path.endswith(".py"):
            continue
        modules[".".join(_python_module_parts(record.path))] = record.path
    return modules


def _python_module_parts(path: str) -> list[str]:
    pure_path = PurePosixPath(path)
    parts = list(pure_path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return parts


def _extract_ts_dependencies(
    path: str,
    content: str,
    index: RepositoryIndex,
) -> set[str]:
    dependencies: set[str] = set()
    for match in TS_IMPORT_RE.finditer(content):
        specifier = match.group(1) or match.group(2)
        if not specifier or not specifier.startswith("."):
            continue
        resolved = _resolve_ts_path(path, specifier, index)
        if resolved:
            dependencies.add(resolved)
    return dependencies


def _resolve_ts_path(
    source_path: str,
    specifier: str,
    index: RepositoryIndex,
) -> str | None:
    source_dir = PurePosixPath(source_path).parent
    target = PurePosixPath(source_dir, specifier)
    normalized = Path(target.as_posix()).as_posix()
    candidates = [normalized]

    for extension in DEFAULT_CONFIG.code_extensions:
        candidates.append(f"{normalized}{extension}")
        candidates.append(f"{normalized}/index{extension}")

    for candidate in candidates:
        if candidate in index.records:
            return candidate
    return None
