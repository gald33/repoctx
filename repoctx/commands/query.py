import argparse
import json
import logging
import sys
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from repoctx.retriever import get_task_context
from repoctx.telemetry import record_repoctx_invocation

NAME = "query"
logger = logging.getLogger(__name__)


def register(subparsers) -> None:
    q = subparsers.add_parser(NAME, help="Retrieve task context (default)")
    q.add_argument("task", help="Task description to retrieve context for")
    q.add_argument("--repo", default=".", help="Repository root")
    q.add_argument("--format", choices=("markdown", "json"), default="markdown")
    q.add_argument("--verbose", action="store_true", help="Debug logging")
    q.add_argument(
        "--debug-scores", action="store_true",
        help="Print per-file score breakdown (heuristic / embedding / final)",
    )
    q.add_argument("--no-embeddings", action="store_true", help="Disable embedding retrieval")
    q.add_argument("--session-id", help="Telemetry session ID")
    q.add_argument("--task-id", help="Telemetry task ID")
    q.add_argument(
        "--variant", choices=("control", "repoctx"), default="repoctx",
        help="Experiment variant label",
    )


def run(args: argparse.Namespace) -> None:
    started = perf_counter()
    session_id = getattr(args, "session_id", None) or uuid4().hex
    task_id = getattr(args, "task_id", None) or uuid4().hex
    debug = getattr(args, "debug_scores", False)
    repo = Path(args.repo)

    embedding_scores = _load_embedding_scores(args.task, repo, args)

    try:
        response = get_task_context(
            task=args.task,
            repo_root=repo,
            embedding_scores=embedding_scores,
        )
    except Exception as exc:
        _record_telemetry(
            task=args.task, repo_root=repo, session_id=session_id,
            task_id=task_id, variant=args.variant, response=None,
            output_format=args.format, success=False,
            error_type=type(exc).__name__,
            duration_ms=int((perf_counter() - started) * 1000),
        )
        logger.error("repoctx failed: %s", exc)
        raise SystemExit(1) from exc

    _record_telemetry(
        task=args.task, repo_root=repo, session_id=session_id,
        task_id=task_id, variant=args.variant, response=response,
        output_format=args.format, success=True, error_type=None,
        duration_ms=int((perf_counter() - started) * 1000),
    )

    if debug:
        _print_debug_scores(response)

    if args.format == "json":
        print(json.dumps(response.to_dict(include_debug=debug), indent=2))
        return
    print(response.context_markdown)


def _load_embedding_scores(
    task: str,
    repo_root: Path,
    args: argparse.Namespace,
) -> dict[str, float] | None:
    if getattr(args, "no_embeddings", False):
        return None
    try:
        from repoctx.embeddings import try_load_retriever

        retriever = try_load_retriever(repo_root)
        if retriever is None:
            return None
        return retriever.query_scores(task)
    except Exception:
        return None


def _print_debug_scores(response) -> None:
    """Write a compact score breakdown table to stderr."""
    sections = [
        ("docs", response.relevant_docs),
        ("files", response.relevant_files),
        ("tests", response.related_tests),
        ("neighbors", response.graph_neighbors),
    ]
    print("\n--- Score breakdown ---", file=sys.stderr)
    for label, items in sections:
        if not items:
            continue
        print(f"\n  [{label}]", file=sys.stderr)
        for item in items:
            emb_part = f"  emb={item.embedding_score:.3f}" if item.embedding_score else ""
            print(
                f"    {item.path:60s}  heur={item.heuristic_score:6.1f}{emb_part}  final={item.score:6.1f}",
                file=sys.stderr,
            )
    print("", file=sys.stderr)


def _record_telemetry(
    *,
    task: str,
    repo_root: Path,
    session_id: str,
    task_id: str,
    variant: str,
    response,
    output_format: str,
    success: bool,
    error_type: str | None,
    duration_ms: int,
) -> None:
    metrics = response.metrics if response is not None else None
    output_bytes = 0
    if response is not None:
        output_text = (
            json.dumps(response.to_dict(), indent=2)
            if output_format == "json"
            else response.context_markdown
        )
        output_bytes = len(output_text.encode("utf-8"))

    try:
        record_repoctx_invocation(
            session_id=session_id,
            task_id=task_id,
            variant=variant,
            surface="cli",
            query=task,
            repo_root=repo_root,
            success=success,
            error_type=error_type,
            repoctx_duration_ms=duration_ms,
            scan_duration_ms=metrics.scan_duration_ms if metrics is not None else 0,
            files_considered=metrics.files_considered if metrics is not None else 0,
            files_selected=metrics.files_selected if metrics is not None else 0,
            docs_selected=metrics.docs_selected if metrics is not None else 0,
            tests_selected=metrics.tests_selected if metrics is not None else 0,
            neighbors_selected=metrics.neighbors_selected if metrics is not None else 0,
            output_format=output_format,
            output_bytes=output_bytes,
        )
    except Exception:
        if logger.isEnabledFor(logging.DEBUG):
            logger.warning("Failed to record telemetry", exc_info=True)
