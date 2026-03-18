from dataclasses import dataclass


DOC_PRIORITY = {
    "agent.md": 10.0,
    "agents.md": 12.0,
    "claude.md": 11.0,
    "gemini.md": 11.0,
    "readme.md": 8.0,
    "architecture.md": 10.0,
    "repo_map.md": 10.0,
    "conventions.md": 9.0,
}

IGNORED_DIRS = (
    ".git",
    ".hg",
    ".mypy_cache",
    ".repoctx",
    ".worktrees",
    ".svn",
    ".next",
    ".playwright-cli",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "venv",
)

SUPPORTED_EXTENSIONS = (
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".mdc",
    ".py",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
)

CODE_EXTENSIONS = (
    ".js",
    ".jsx",
    ".py",
    ".ts",
    ".tsx",
)

CONFIG_EXTENSIONS = (
    ".json",
    ".yaml",
    ".yml",
)

TEST_MARKERS = (
    ".spec.",
    ".test.",
)

STOPWORDS = {
    "a",
    "add",
    "an",
    "and",
    "for",
    "in",
    "into",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}


@dataclass(frozen=True, slots=True)
class RepoCtxConfig:
    ignored_dirs: tuple[str, ...] = IGNORED_DIRS
    supported_extensions: tuple[str, ...] = SUPPORTED_EXTENSIONS
    code_extensions: tuple[str, ...] = CODE_EXTENSIONS
    config_extensions: tuple[str, ...] = CONFIG_EXTENSIONS
    test_markers: tuple[str, ...] = TEST_MARKERS
    max_file_bytes: int = 16_000
    max_docs: int = 6
    max_files: int = 8
    max_tests: int = 6
    max_neighbors: int = 8
    embedding_weight: float = 12.0
    embedding_qualify_threshold: float = 0.3


DEFAULT_CONFIG = RepoCtxConfig()


@dataclass(frozen=True, slots=True)
class EmbeddingConfig:
    model_name: str = "Qwen/Qwen3-Embedding-0.6B"
    max_content_chars: int = 8000
    index_dir: str = ".repoctx"


DEFAULT_EMBEDDING_CONFIG = EmbeddingConfig()
