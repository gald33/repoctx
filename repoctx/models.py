from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


FileKind = Literal["code", "config", "doc", "other", "test"]


@dataclass(slots=True)
class FileRecord:
    path: str
    absolute_path: Path
    extension: str
    kind: FileKind
    content: str = ""
    doc_score: float = 0.0

    @property
    def name(self) -> str:
        return Path(self.path).name

    @property
    def stem(self) -> str:
        return Path(self.path).stem


@dataclass(slots=True)
class RepositoryIndex:
    root: Path
    records: dict[str, FileRecord] = field(default_factory=dict)
    docs: list[FileRecord] = field(default_factory=list)
    code_files: list[FileRecord] = field(default_factory=list)
    test_files: list[FileRecord] = field(default_factory=list)
    config_files: list[FileRecord] = field(default_factory=list)


@dataclass(slots=True)
class RankedPath:
    path: str
    reason: str
    score: float
    snippet: str | None = None
    heuristic_score: float = 0.0
    embedding_score: float = 0.0

    def to_dict(self, include_debug: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "path": self.path,
            "reason": self.reason,
            "score": self.score,
        }
        if self.snippet is not None:
            data["snippet"] = self.snippet
        if include_debug:
            data["heuristic_score"] = round(self.heuristic_score, 3)
            data["embedding_score"] = round(self.embedding_score, 3)
        return data


@dataclass(slots=True)
class DependencyGraph:
    forward: dict[str, set[str]] = field(default_factory=dict)
    reverse: dict[str, set[str]] = field(default_factory=dict)


@dataclass(slots=True)
class ContextMetrics:
    files_considered: int = 0
    files_selected: int = 0
    docs_selected: int = 0
    tests_selected: int = 0
    neighbors_selected: int = 0
    scan_duration_ms: int = 0
    output_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ContextResponse:
    summary: str
    relevant_docs: list[RankedPath]
    relevant_files: list[RankedPath]
    related_tests: list[RankedPath]
    graph_neighbors: list[RankedPath]
    context_markdown: str
    metrics: ContextMetrics = field(default_factory=ContextMetrics)

    def to_dict(
        self,
        include_metrics: bool = False,
        include_debug: bool = False,
    ) -> dict[str, Any]:
        data = {
            "summary": self.summary,
            "relevant_docs": [item.to_dict(include_debug) for item in self.relevant_docs],
            "relevant_files": [item.to_dict(include_debug) for item in self.relevant_files],
            "related_tests": [item.to_dict(include_debug) for item in self.related_tests],
            "graph_neighbors": [item.to_dict(include_debug) for item in self.graph_neighbors],
            "context_markdown": self.context_markdown,
        }
        if include_metrics:
            data["metrics"] = self.metrics.to_dict()
        return data
