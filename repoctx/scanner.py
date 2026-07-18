import logging
import os
import re
from pathlib import Path, PurePosixPath

from repoctx.config import DEFAULT_CONFIG, DOC_PRIORITY, RepoCtxConfig
from repoctx.models import FileRecord, RepositoryIndex
from repoctx.subkinds import classify_subkind

logger = logging.getLogger(__name__)


def scan_repository(
    repo_root: str | Path,
    config: RepoCtxConfig = DEFAULT_CONFIG,
) -> RepositoryIndex:
    root = Path(repo_root).resolve()
    if not root.exists():
        raise FileNotFoundError(f"Repository root does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Repository root is not a directory: {root}")
    index = RepositoryIndex(root=root)

    for path in _iter_files(root, config):
        rel_path = path.relative_to(root).as_posix()
        content, import_source = _read_text_and_imports(path, config.max_file_bytes)
        record = build_file_record(rel_path, content, root, config, import_source=import_source)
        _add_record(index, record)

    index.docs.sort(key=lambda item: (-item.doc_score, item.path))
    return index


def build_file_record(
    rel_path: str,
    content: str,
    root: str | Path,
    config: RepoCtxConfig = DEFAULT_CONFIG,
    import_source: str = "",
) -> FileRecord:
    """Classify a single file into a :class:`FileRecord` from its content.

    Shared by the working-tree scan and the git-object scan
    (:func:`repoctx.git_tree.scan_git_tree`) so both classify identically.
    ``absolute_path`` is the working-tree location (which may not exist on disk
    when the record came from a git blob in another branch).
    """
    extension = PurePosixPath(rel_path).suffix.lower()
    kind = _classify_file(rel_path, extension, config)
    doc_score = _score_doc(rel_path) if kind == "doc" else 0.0
    subkind = classify_subkind(kind, rel_path, content)
    return FileRecord(
        path=rel_path,
        absolute_path=Path(root) / rel_path,
        extension=extension,
        kind=kind,
        subkind=subkind,
        content=content,
        import_source=import_source,
        doc_score=doc_score,
    )


def _add_record(index: RepositoryIndex, record: FileRecord) -> None:
    index.records[record.path] = record
    if record.kind == "doc":
        index.docs.append(record)
    elif record.kind == "code":
        index.code_files.append(record)
    elif record.kind == "test":
        index.test_files.append(record)
    elif record.kind == "config":
        index.config_files.append(record)


def is_supported_path(rel_path: str, config: RepoCtxConfig = DEFAULT_CONFIG) -> bool:
    """True if ``rel_path`` is one repoctx would scan (extension + not ignored)."""
    pure = PurePosixPath(rel_path)
    if pure.suffix.lower() not in config.supported_extensions:
        return False
    return not any(part in config.ignored_dirs for part in pure.parts)


def _iter_files(root: Path, config: RepoCtxConfig) -> list[Path]:
    files: list[Path] = []
    ignored = set(config.ignored_dirs)

    for current_root, dirnames, filenames in os.walk(root):
        # Prune ignored names *and* virtualenvs detected structurally. The name
        # blocklist only knows ``venv``/``.venv``, so a virtualenv called
        # anything else (``myenv``, ``env311``, …) let its whole vendored
        # site-packages tree into the index — thousands of third-party files
        # that aren't the user's code. PEP 405 guarantees a venv root contains
        # ``pyvenv.cfg``, which is name-independent and costs one stat per dir.
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in ignored and not (Path(current_root) / name / "pyvenv.cfg").exists()
        )
        for filename in sorted(filenames):
            path = Path(current_root) / filename
            if path.suffix.lower() not in config.supported_extensions:
                continue
            files.append(path)
    return files


def _classify_file(path: str, extension: str, config: RepoCtxConfig) -> str:
    normalized = path.lower()
    name = PurePosixPath(path).name.lower()

    if extension in {".md", ".mdc"}:
        return "doc"

    if _is_test_path(normalized, config):
        return "test"

    if ".config." in name and extension in config.code_extensions:
        return "config"

    if extension in config.code_extensions:
        return "code"

    if extension in config.config_extensions:
        return "config"

    return "other"


def _is_test_path(path: str, config: RepoCtxConfig) -> bool:
    parts = PurePosixPath(path).parts
    if "tests" in parts or "__tests__" in parts:
        return True
    return any(marker in path for marker in config.test_markers)


def _score_doc(path: str) -> float:
    pure_path = PurePosixPath(path)
    name = pure_path.name.lower()
    score = DOC_PRIORITY.get(name, 2.0)
    depth = len(pure_path.parts) - 1

    if depth == 0:
        score += 6.0
    elif pure_path.parts[0].lower() == "docs":
        score += 3.0
    else:
        score += max(0.0, 3.0 - float(depth))

    lowered = path.lower()
    if "agent" in lowered or "architecture" in lowered or "convention" in lowered:
        score += 2.0

    return score


def _read_text(path: Path, max_bytes: int) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:max_bytes]
    except OSError as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return ""


# A Python import statement always begins its logical line with `import` or
# `from` (indented for function-local/deferred imports, which this codebase
# uses heavily).
_PY_IMPORT_LINE_RE = re.compile(r"^[ \t]*(?:import|from)[ \t]")


def _read_text_and_imports(path: Path, max_bytes: int) -> tuple[str, str]:
    """Return ``(content, import_source)`` in a single read.

    ``content`` is capped at ``max_bytes`` as before. ``import_source`` holds
    the import-bearing lines from the *whole* file, so the dependency graph
    still sees imports that live past the cap in a large module. Python only —
    the TS extractor matches across lines and can't be line-filtered safely.
    """
    try:
        full = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return "", ""

    content = full[:max_bytes]
    if path.suffix.lower() != ".py" or len(full) <= max_bytes:
        # Nothing truncated (or not Python): `content` already has every import.
        return content, ""
    return content, _harvest_import_lines(full)


# Bound on how far a single import statement may be followed. Matches
# `graph._MAX_CONTINUATION_LINES`.
_MAX_CONTINUATION_LINES = 50


def _harvest_import_lines(text: str) -> str:
    """Import statements from ``text``, continuation lines included.

    A plain line filter would keep ``from x import (`` but drop the indented
    names beneath it, so a parenthesized import would arrive at the graph with
    an empty clause. Statements are emitted contiguously so continuations stay
    intact.

    Each statement's opening line is dedented: the graph parses this text with
    ``ast``, and a function-local ``from x import y`` carried over with its
    original indentation is an ``IndentationError`` at module level — which
    would silently drop every large file back to the regex fallback.
    Continuation lines keep their indentation, which is irrelevant inside
    brackets or after a backslash.
    """
    lines = text.splitlines()
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not _PY_IMPORT_LINE_RE.match(line):
            index += 1
            continue

        out.append(line.lstrip())
        depth = line.count("(") - line.count(")")
        continued = line.rstrip().endswith("\\")
        index += 1
        followed = 0
        while (depth > 0 or continued) and index < len(lines) and followed < _MAX_CONTINUATION_LINES:
            nxt = lines[index]
            out.append(nxt)
            depth += nxt.count("(") - nxt.count(")")
            continued = nxt.rstrip().endswith("\\")
            index += 1
            followed += 1
    return "\n".join(out)
