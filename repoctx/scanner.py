import logging
import os
from pathlib import Path, PurePosixPath

from repoctx.config import DEFAULT_CONFIG, DOC_PRIORITY, RepoCtxConfig
from repoctx.models import FileRecord, RepositoryIndex

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
        extension = path.suffix.lower()
        kind = _classify_file(rel_path, extension, config)
        doc_score = _score_doc(rel_path) if kind == "doc" else 0.0
        content = _read_text(path, config.max_file_bytes)
        record = FileRecord(
            path=rel_path,
            absolute_path=path,
            extension=extension,
            kind=kind,
            content=content,
            doc_score=doc_score,
        )
        index.records[rel_path] = record
        if kind == "doc":
            index.docs.append(record)
        elif kind == "code":
            index.code_files.append(record)
        elif kind == "test":
            index.test_files.append(record)
        elif kind == "config":
            index.config_files.append(record)

    index.docs.sort(key=lambda item: (-item.doc_score, item.path))
    return index


def _iter_files(root: Path, config: RepoCtxConfig) -> list[Path]:
    files: list[Path] = []
    ignored = set(config.ignored_dirs)

    for current_root, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in ignored)
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
