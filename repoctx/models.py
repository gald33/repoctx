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

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if data["snippet"] is None:
            data.pop("snippet")
        return data


@dataclass(slots=True)
class DependencyGraph:
    forward: dict[str, set[str]] = field(default_factory=dict)
    reverse: dict[str, set[str]] = field(default_factory=dict)


@dataclass(slots=True)
class ContextResponse:
    summary: str
    relevant_docs: list[RankedPath]
    relevant_files: list[RankedPath]
    related_tests: list[RankedPath]
    graph_neighbors: list[RankedPath]
    context_markdown: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "relevant_docs": [item.to_dict() for item in self.relevant_docs],
            "relevant_files": [item.to_dict() for item in self.relevant_files],
            "related_tests": [item.to_dict() for item in self.related_tests],
            "graph_neighbors": [item.to_dict() for item in self.graph_neighbors],
            "context_markdown": self.context_markdown,
        }
