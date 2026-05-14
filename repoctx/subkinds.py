"""Subkind classifier — refines coarse kinds (code/doc/config) into
explainable sub-buckets the Phase 3 tuner can fit separately.

Design notes:
- **Deterministic, no ML.** Each detector is a pure function over (path,
  content_sample). First-match wins, ordered specific → general.
- **Path-first.** Path patterns drive most decisions because they're stable
  across edits and don't require reading file content. Content-based rules
  (import sniffing, header marker detection) only fire when the path is
  ambiguous.
- **Hierarchical fallback.** The tuner looks up thresholds by full key
  ``"code/handler"`` first, then parent ``"code"``, then ``"_default"`` —
  so sub-kinds only "activate" once a cell collects enough labels. A
  brand-new repo gets parent-kind behavior with zero configuration.
- **Per-repo override path.** A user can extend the classifier without
  editing code via ``.repoctx/config.json`` (``{"subkinds": {"handler":
  {"paths": ["server/api/"]}}}``); applied as a pre-pass before the
  built-in rules. Phase 1 wires the data model; the override loader is a
  separate small change.

Subkind labels by parent kind:

- ``code``: handler / model / cli / util / scaffold / generated / other
- ``doc``: agent_contract / architecture / readme / other
- ``config``: build / ci / lint / other
- ``test``: (flat — embedding geometry inside tests is less differentiated;
  classifier returns "" for tests so the tuner stays at the parent kind)
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Callable

# How many bytes of file content to peek at for header / import detection.
# Bounded so the classifier stays cheap on huge generated files.
HEADER_SAMPLE_BYTES = 2048


# --- code subkinds ----------------------------------------------------------

_WEB_FRAMEWORK_IMPORTS = re.compile(
    r"^\s*(?:from|import)\s+(?:fastapi|flask|starlette|django|aiohttp|sanic|tornado|"
    r"express|koa|hapi|nestjs|hono)\b",
    re.MULTILINE | re.IGNORECASE,
)
_MODEL_IMPORTS = re.compile(
    r"^\s*(?:from|import)\s+(?:pydantic|dataclasses|sqlalchemy|sqlmodel|attrs|"
    r"marshmallow|prisma|typeorm|sequelize|drizzle)\b",
    re.MULTILINE | re.IGNORECASE,
)
_CLI_IMPORTS = re.compile(
    r"^\s*(?:from|import)\s+(?:argparse|click|typer|commander|yargs|oclif)\b",
    re.MULTILINE | re.IGNORECASE,
)
_GENERATED_MARKERS = re.compile(
    r"\b(?:GENERATED|DO\s*NOT\s*EDIT|auto-?generated|@?codegen)\b",
    re.IGNORECASE,
)
_HAS_CLASS_DEF = re.compile(r"^\s*class\s+\w+\b", re.MULTILINE)

_CODE_HANDLER_DIRS = ("routes", "handlers", "api", "endpoints", "controllers", "views")
_CODE_MODEL_DIRS = ("models", "schemas", "types", "entities", "domain")
_CODE_CLI_DIRS = ("commands", "cli", "bin")
_CODE_UTIL_DIRS = ("utils", "util", "lib", "helpers", "common")
_SCAFFOLD_NAMES = frozenset({"__init__.py", "conftest.py", "setup.py", "manage.py"})


def _has_segment(path_parts: tuple[str, ...], names: tuple[str, ...]) -> bool:
    return any(p.lower() in names for p in path_parts)


def _code_subkind(path: str, content: str) -> str:
    """Classify a code file into one of seven sub-buckets.

    Order matters: generated/scaffold first (cheapest+most specific), then
    path-based handler/model/cli/util, then content-based fallbacks for
    ambiguous flat layouts, then ``other``.
    """
    name = PurePosixPath(path).name
    parts = PurePosixPath(path).parts

    # Generated — checked first because it overrides any other classification.
    if content:
        head = content[:HEADER_SAMPLE_BYTES]
        if _GENERATED_MARKERS.search(head):
            return "generated"

    if name in _SCAFFOLD_NAMES:
        return "scaffold"

    if _has_segment(parts, _CODE_HANDLER_DIRS):
        return "handler"
    if _has_segment(parts, _CODE_MODEL_DIRS):
        return "model"
    if _has_segment(parts, _CODE_CLI_DIRS):
        return "cli"
    if _has_segment(parts, _CODE_UTIL_DIRS):
        return "util"

    # Content-based fallbacks for repos that don't use conventional dirs.
    if content:
        head = content[:HEADER_SAMPLE_BYTES]
        if _WEB_FRAMEWORK_IMPORTS.search(head):
            return "handler"
        if _CLI_IMPORTS.search(head):
            return "cli"
        if _MODEL_IMPORTS.search(head):
            return "model"
        if not _HAS_CLASS_DEF.search(head):
            # No classes, no obvious framework → likely a utility module.
            return "util"

    return "other"


# --- doc subkinds -----------------------------------------------------------

_AGENT_CONTRACT_NAMES = frozenset({
    "agents.md", "agent.md", "claude.md", "gemini.md", "codex.md", "copilot.md",
})
_ARCHITECTURE_DIRS = ("architecture", "adr", "rfcs", "design")


def _doc_subkind(path: str, content: str) -> str:
    name = PurePosixPath(path).name.lower()
    parts = tuple(p.lower() for p in PurePosixPath(path).parts)
    if name in _AGENT_CONTRACT_NAMES:
        return "agent_contract"
    if _has_segment(parts, _ARCHITECTURE_DIRS) or name in {"architecture.md", "design.md"}:
        return "architecture"
    if name.startswith("readme."):
        return "readme"
    return "other"


# --- config subkinds --------------------------------------------------------

_BUILD_FILES = frozenset({
    "pyproject.toml", "setup.cfg", "setup.py", "package.json", "package-lock.json",
    "yarn.lock", "pnpm-lock.yaml", "cargo.toml", "go.mod", "go.sum", "makefile",
    "tsconfig.json", "tsconfig.base.json", "rollup.config.js", "vite.config.ts",
    "webpack.config.js", "build.gradle", "pom.xml",
})
_CI_DIRS = (".github", ".gitlab", "circleci", ".circleci", "azure-pipelines")
_LINT_FILES = frozenset({
    ".eslintrc", ".eslintrc.json", ".eslintrc.js", ".prettierrc",
    ".prettierrc.json", "ruff.toml", "mypy.ini", ".flake8", "pyrightconfig.json",
    ".editorconfig",
})


def _config_subkind(path: str, content: str) -> str:
    name = PurePosixPath(path).name.lower()
    parts = tuple(p.lower() for p in PurePosixPath(path).parts)
    if name in _BUILD_FILES:
        return "build"
    if _has_segment(parts, _CI_DIRS):
        return "ci"
    if name in _LINT_FILES or name.startswith(".eslintrc.") or name.startswith(".prettierrc."):
        return "lint"
    return "other"


# --- public entry points ----------------------------------------------------

_SUBKIND_DISPATCH: dict[str, Callable[[str, str], str]] = {
    "code": _code_subkind,
    "doc": _doc_subkind,
    "config": _config_subkind,
}


def classify_subkind(kind: str, path: str, content: str = "") -> str:
    """Return the subkind for a (kind, path, content) triple.

    Returns ``""`` (empty string) for kinds that don't have refinements yet
    (currently: ``test``, ``other``). Callers treat an empty subkind as
    "use parent-kind threshold only".
    """
    detector = _SUBKIND_DISPATCH.get(kind)
    if detector is None:
        return ""
    return detector(path, content)


def full_kind(kind: str, subkind: str) -> str:
    """Compose the hierarchical key used in threshold maps and the event log.

    Example: ``full_kind("code", "handler") == "code/handler"``. An empty
    subkind yields just the parent kind, so callers don't need to special-case
    test files or flat kinds.
    """
    if subkind:
        return f"{kind}/{subkind}"
    return kind


def parent_kind(full: str) -> str:
    """Inverse of :func:`full_kind` — strip the subkind, keep the parent."""
    return full.split("/", 1)[0]


__all__ = [
    "HEADER_SAMPLE_BYTES",
    "classify_subkind",
    "full_kind",
    "parent_kind",
]
