import logging
import re
from pathlib import Path
from time import perf_counter

from repoctx.config import DEFAULT_CONFIG, STOPWORDS, RepoCtxConfig
from repoctx.context_pack import render_context_markdown
from repoctx.graph import build_dependency_graph, expand_graph_neighbors
from repoctx.models import ContextMetrics, ContextResponse, DependencyGraph, FileRecord, RankedPath, RepositoryIndex
from repoctx.scanner import scan_repository

logger = logging.getLogger(__name__)

TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def get_task_context(
    task: str,
    repo_root: str | Path = ".",
    config: RepoCtxConfig = DEFAULT_CONFIG,
    embedding_scores: dict[str, float] | None = None,
) -> ContextResponse:
    scan_started = perf_counter()
    index = scan_repository(repo_root, config=config)
    scan_duration_ms = int((perf_counter() - scan_started) * 1000)
    graph = build_dependency_graph(index)
    response = get_task_context_data(
        task=task, index=index, graph=graph, config=config,
        embedding_scores=embedding_scores,
    )
    response.metrics.files_considered = len(index.records)
    response.metrics.scan_duration_ms = scan_duration_ms
    return response


def get_task_context_data(
    task: str,
    index: RepositoryIndex,
    graph: DependencyGraph,
    config: RepoCtxConfig = DEFAULT_CONFIG,
    embedding_scores: dict[str, float] | None = None,
) -> ContextResponse:
    relevant_docs = rank_documents(index, task, config, embedding_scores)
    relevant_files = rank_files(index, task, config, embedding_scores)
    related_tests = find_related_tests(index, relevant_files, graph, config)
    graph_neighbors = expand_graph_neighbors(
        index=index,
        graph=graph,
        seed_paths=[item.path for item in relevant_files],
        config=config,
    )

    summary = _build_summary(task, relevant_docs, relevant_files, related_tests, graph_neighbors)
    response = ContextResponse(
        summary=summary,
        relevant_docs=relevant_docs,
        relevant_files=relevant_files,
        related_tests=related_tests,
        graph_neighbors=graph_neighbors,
        context_markdown="",
    )
    response.context_markdown = render_context_markdown(response)
    response.metrics = ContextMetrics(
        files_selected=len(response.relevant_files),
        docs_selected=len(response.relevant_docs),
        tests_selected=len(response.related_tests),
        neighbors_selected=len(response.graph_neighbors),
        output_bytes=len(response.context_markdown.encode("utf-8")),
    )
    return response


def rank_documents(
    index: RepositoryIndex,
    task: str,
    config: RepoCtxConfig = DEFAULT_CONFIG,
    embedding_scores: dict[str, float] | None = None,
) -> list[RankedPath]:
    task_tokens = set(tokenize(task))
    ranked: list[RankedPath] = []

    for record in index.docs:
        file_tokens = set(tokenize(record.path))
        content_tokens = set(tokenize(record.content))
        path_overlap = sorted(task_tokens & file_tokens)
        content_overlap = sorted(task_tokens & content_tokens)
        overlap = sorted(task_tokens & (file_tokens | content_tokens))
        heuristic_score = record.doc_score + (4.0 * len(path_overlap)) + (1.0 * len(content_overlap))

        emb_score = (embedding_scores or {}).get(record.path, 0.0)
        emb_boost = config.embedding_weight * max(0.0, emb_score) if embedding_scores else 0.0
        score = heuristic_score + emb_boost

        has_emb_signal = embedding_scores is not None and emb_score >= config.embedding_qualify_threshold
        if not has_emb_signal:
            if not overlap and record.doc_score < 12.0:
                continue
            if not path_overlap and len(content_overlap) < 2 and record.doc_score < 12.0:
                continue
        if score <= 0:
            continue
        reason = _build_reason(
            overlap=overlap,
            default_reason="High-value documentation for repository context",
        )
        ranked.append(
            RankedPath(
                path=record.path,
                reason=reason,
                score=score,
                snippet=_select_snippet(record, overlap),
                heuristic_score=heuristic_score,
                embedding_score=emb_score,
            )
        )

    ranked.sort(key=lambda item: (-item.score, item.path))
    return ranked[: config.max_docs]


def rank_files(
    index: RepositoryIndex,
    task: str,
    config: RepoCtxConfig = DEFAULT_CONFIG,
    embedding_scores: dict[str, float] | None = None,
) -> list[RankedPath]:
    task_tokens = set(tokenize(task))
    ranked: list[RankedPath] = []
    candidates = index.code_files + index.config_files

    for record in candidates:
        path_tokens = set(tokenize(record.path))
        content_tokens = set(tokenize(record.content))
        name_tokens = set(tokenize(record.name))

        name_overlap = sorted(task_tokens & name_tokens)
        path_overlap = sorted(task_tokens & path_tokens)
        content_overlap = sorted(task_tokens & content_tokens)
        heuristic_score = (6.0 * len(name_overlap)) + (3.0 * len(path_overlap)) + (1.0 * len(content_overlap))
        if record.kind == "config":
            heuristic_score *= 0.5

        emb_score = (embedding_scores or {}).get(record.path, 0.0)
        emb_boost = config.embedding_weight * max(0.0, emb_score) if embedding_scores else 0.0
        score = heuristic_score + emb_boost

        has_emb_signal = embedding_scores is not None and emb_score >= config.embedding_qualify_threshold
        if not has_emb_signal:
            if not (name_overlap or path_overlap) and len(content_overlap) < 2:
                continue
        if score <= 0:
            continue
        overlap = name_overlap or path_overlap or content_overlap
        reason = _build_reason(
            overlap=overlap,
            default_reason="Task tokens align with file name and content",
        )
        ranked.append(
            RankedPath(
                path=record.path,
                reason=reason,
                score=score,
                snippet=_select_snippet(record, overlap),
                heuristic_score=heuristic_score,
                embedding_score=emb_score,
            )
        )

    ranked.sort(key=lambda item: (-item.score, item.path))
    return ranked[: config.max_files]


def find_related_tests(
    index: RepositoryIndex,
    relevant_files: list[RankedPath],
    graph: DependencyGraph,
    config: RepoCtxConfig = DEFAULT_CONFIG,
) -> list[RankedPath]:
    seeds = {item.path: item for item in relevant_files}
    ranked: list[RankedPath] = []

    for record in index.test_files:
        score = 0.0
        reasons: list[str] = []
        test_stem = normalize_test_stem(record.stem)
        reverse_edges = graph.forward.get(record.path, set())

        for seed_path in seeds:
            seed_stem = normalize_test_stem(Path(seed_path).stem)
            if test_stem and seed_stem and test_stem == seed_stem:
                score += 5.0
                reasons.append(f"Stem matches `{seed_path}`")
            if seed_path in reverse_edges:
                score += 6.0
                reasons.append(f"Imports `{seed_path}`")
            if Path(seed_path).parent.name in record.path:
                score += 1.0

        if score <= 0:
            continue
        ranked.append(
            RankedPath(
                path=record.path,
                reason="; ".join(dict.fromkeys(reasons)) or "Likely associated test",
                score=score,
            )
        )

    ranked.sort(key=lambda item: (-item.score, item.path))
    return ranked[: config.max_tests]


def tokenize(text: str) -> list[str]:
    tokens = [match.group(0).lower() for match in TOKEN_RE.finditer(text)]
    return [token for token in tokens if token not in STOPWORDS and len(token) > 1]


def normalize_test_stem(stem: str) -> str:
    normalized = stem.lower()
    for prefix in ("test_", "test-"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
    for suffix in (".test", ".spec", "_test", "-test", "_spec", "-spec"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
    return normalized


def _build_reason(overlap: list[str], default_reason: str) -> str:
    if not overlap:
        return default_reason
    visible = ", ".join(overlap[:3])
    return f"Matches task tokens: {visible}"


def _select_snippet(record: FileRecord, overlap: list[str]) -> str | None:
    lines = [line.strip() for line in record.content.splitlines() if line.strip()]
    if not lines:
        return None
    overlap_set = set(overlap)
    for line in lines:
        lowered = line.lower()
        if any(token in lowered for token in overlap_set):
            return line[:160]
    return lines[0][:160]


def _build_summary(
    task: str,
    relevant_docs: list[RankedPath],
    relevant_files: list[RankedPath],
    related_tests: list[RankedPath],
    graph_neighbors: list[RankedPath],
) -> str:
    return (
        f"Identified {len(relevant_docs)} docs, {len(relevant_files)} files, "
        f"{len(related_tests)} tests, and {len(graph_neighbors)} graph neighbors "
        f"that look relevant to '{task}'."
    )
