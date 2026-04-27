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
    ".claude",  # Claude Code's settings + worktree checkouts (.claude/worktrees/...)
    ".git",
    ".hg",
    ".mypy_cache",
    ".repoctx",
    ".worktrees",  # standalone .worktrees/ at any depth (catches non-.claude variants)
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
    # device: None = auto-detect (sentence-transformers default), or
    # "cpu" / "cuda" / "mps". Overridable via REPOCTX_EMBEDDING_DEVICE.
    # MPS auto-detection has been a source of OOM on Apple silicon when
    # encoding many chunks in one batch; "cpu" is the safe fallback.
    device: str | None = None
    # Batch size for encode_documents. Larger = faster but more peak RAM
    # and (on MPS) larger Metal buffers. Override via REPOCTX_EMBEDDING_BATCH_SIZE.
    batch_size: int = 16
    # Maximum tokens per row. Attention activations scale as seq_len², so
    # halving this from a typical 512 cuts peak memory ~4×. Most code chunks
    # fit in 256 tokens; longer chunks are truncated. Override via
    # REPOCTX_EMBEDDING_MAX_SEQ_LENGTH.
    max_seq_length: int = 256
    # Weight/activation dtype: "auto" picks fp16 on MPS/CUDA, fp32 on CPU
    # (fp16 on CPU is slower than fp32 in PyTorch). "fp32" / "fp16" pin the
    # choice. Override via REPOCTX_EMBEDDING_DTYPE.
    dtype: str = "auto"


DEFAULT_EMBEDDING_CONFIG = EmbeddingConfig()
