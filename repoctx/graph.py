import re
from collections import defaultdict
from pathlib import Path, PurePosixPath

from repoctx.config import DEFAULT_CONFIG, RepoCtxConfig
from repoctx.models import DependencyGraph, RankedPath, RepositoryIndex

PYTHON_FROM_RE = re.compile(r"^\s*from\s+([.\w]+)\s+import\s+", re.MULTILINE)
PYTHON_IMPORT_RE = re.compile(r"^\s*import\s+([.\w\s,]+)", re.MULTILINE)
TS_IMPORT_RE = re.compile(
    r"""(?:import|export)\s+(?:[^'"]+?\s+from\s+)?['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\)"""
)


def build_dependency_graph(index: RepositoryIndex) -> DependencyGraph:
    forward: dict[str, set[str]] = defaultdict(set)
    reverse: dict[str, set[str]] = defaultdict(set)
    python_modules = _build_python_module_map(index)

    for record in index.code_files + index.test_files:
        for dependency in _extract_dependencies(record.path, record.content, index, python_modules):
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
) -> set[str]:
    if path.endswith(".py"):
        return _extract_python_dependencies(path, content, python_modules)
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
        resolved = _resolve_python_import(
            import_path=match.group(1),
            current_parts=module_parts,
            is_package=is_package,
            module_map=python_modules,
        )
        if resolved:
            dependencies.add(resolved)

    for match in PYTHON_IMPORT_RE.finditer(content):
        modules = [item.strip().split()[0] for item in match.group(1).split(",")]
        for module_name in modules:
            resolved = python_modules.get(module_name)
            if resolved:
                dependencies.add(resolved)

    return dependencies


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
