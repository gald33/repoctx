from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType


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
    ".uv-cache",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "site-packages",  # vendored deps without a pyvenv.cfg alongside them
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


# Kinds carried by FileRecord.kind ("code" / "doc" / "config" / "test") plus a
# "_default" fallback used when a record's kind isn't represented in the map.
# Keeping these as MappingProxyType makes the dataclass-default safe to share
# across instances without risking mutation.
_DEFAULT_QUALIFY_THRESHOLDS: Mapping[str, float] = MappingProxyType({
    "code": 0.3,
    "doc": 0.3,
    "config": 0.3,
    "test": 0.3,
    "_default": 0.3,
})

_DEFAULT_LEXICAL_TIEBREAKS: Mapping[str, float] = MappingProxyType({
    "code": 0.05,
    "doc": 0.05,
    "config": 0.05,
    "test": 0.05,
    "_default": 0.05,
})


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
    # Per-kind cosine floor for an embedding hit to qualify as a semantic
    # match. The retriever looks up by FileRecord.kind; missing kinds fall
    # back to "_default".
    embedding_qualify_thresholds: Mapping[str, float] = field(
        default_factory=lambda: dict(_DEFAULT_QUALIFY_THRESHOLDS)
    )
    # Per-kind weight scaling the normalized lexical heuristic when it's
    # acting as a tiebreaker on top of cosine similarity.
    lexical_tiebreak_weights: Mapping[str, float] = field(
        default_factory=lambda: dict(_DEFAULT_LEXICAL_TIEBREAKS)
    )

    # Per-bundle probability of including 1-2 sub-threshold candidates so
    # the Phase 3 tuner can see what the current threshold is filtering out.
    # Without this, the loop tunes itself toward whatever it already admits
    # and is structurally blind to "lower the threshold" signals.
    exploration_epsilon: float = 0.05

    def qualify_threshold_for(self, kind: str, subkind: str = "") -> float:
        """Resolve the threshold by walking the hierarchical fallback chain.

        Order: ``kind/subkind`` (e.g. ``code/handler``) → parent ``kind``
        (e.g. ``code``) → ``_default``. The first key present in the map
        wins, so per-subkind tuning is "free" once a cell collects labels
        and silently inactive otherwise.
        """
        m = self.embedding_qualify_thresholds
        if subkind:
            full = f"{kind}/{subkind}"
            if full in m:
                return m[full]
        if kind in m:
            return m[kind]
        return m.get("_default", 0.3)

    def lexical_tiebreak_for(self, kind: str, subkind: str = "") -> float:
        m = self.lexical_tiebreak_weights
        if subkind:
            full = f"{kind}/{subkind}"
            if full in m:
                return m[full]
        if kind in m:
            return m[kind]
        return m.get("_default", 0.05)


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
    # Auto-flush queue: harness hooks / agents append edits via `repoctx update`,
    # which embeds in batches once a threshold is reached. Complements
    # `repoctx index --incremental` (bulk catch-up); the queue handles live
    # per-edit upkeep so the index doesn't drift between bulk runs.
    auto_flush: bool = True
    debounce_n: int = 10
    debounce_max_age_seconds: int = 300
    queue_filename: str = ".pending"
    # Authoritative index is pinned to origin/main. These gate how the read
    # path keeps it fresh without paying a fetch (or re-embed) on every call.
    # TTL between `git fetch origin main` attempts on the read path.
    base_fetch_ttl_seconds: int = 1800
    # When origin/main advances past the indexed base, re-embed the delta on
    # the next read (TTL-gated). Off → only a staleness warning is surfaced and
    # the user must run `repoctx index --refresh` explicitly.
    base_refresh_on_read: bool = True
    # Skip the on-read re-embed (warn instead) when more than this many files
    # changed — a large delta means `repoctx index` is the better call.
    base_refresh_max_files: int = 200
    # Overlay the current worktree's delta (commits ahead of origin/main +
    # uncommitted edits) on top of the origin/main base at query time, so
    # in-progress work is retrievable as if already rebased. Off → retrieval
    # reflects pure origin/main.
    overlay_worktree: bool = True
    # Safety cap: skip the overlay (too expensive) past this many delta files.
    overlay_max_files: int = 300
    # Advisory lane: a SEPARATE, opt-in index over committed branch tips ahead
    # of origin/main ("is this already being done elsewhere?"). Never mixed
    # into authoritative results. These bound which branches qualify.
    advisory_max_age_days: int = 30
    advisory_max_branches: int = 25
    advisory_max_files_per_branch: int = 60


DEFAULT_EMBEDDING_CONFIG = EmbeddingConfig()
