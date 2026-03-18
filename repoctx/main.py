import argparse
import json
import logging
import sys
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from repoctx.config import DEFAULT_EMBEDDING_CONFIG
from repoctx.retriever import get_task_context
from repoctx.telemetry import record_repoctx_invocation

logger = logging.getLogger(__name__)

SUBCOMMANDS = {"query", "index", "update", "rebuild"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Local repository intelligence for coding agents",
    )
    sub = parser.add_subparsers(dest="command")

    # -- query (default when first arg isn't a subcommand) --------------------
    q = sub.add_parser("query", help="Retrieve task context (default)")
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

    # -- index ----------------------------------------------------------------
    idx = sub.add_parser("index", help="Build the embedding index for a repo")
    idx.add_argument("--repo", default=".", help="Repository root")
    idx.add_argument("--verbose", action="store_true")

    # -- update ---------------------------------------------------------------
    upd = sub.add_parser("update", help="Re-embed a single file")
    upd.add_argument("file", help="Relative path of the file to update")
    upd.add_argument("--repo", default=".", help="Repository root")
    upd.add_argument("--verbose", action="store_true")

    # -- rebuild --------------------------------------------------------------
    rb = sub.add_parser("rebuild", help="Delete and rebuild the embedding index")
    rb.add_argument("--repo", default=".", help="Repository root")
    rb.add_argument("--verbose", action="store_true")

    return parser


def main() -> None:
    _ensure_default_subcommand()
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if getattr(args, "verbose", False) else logging.WARNING)

    cmd = args.command or "query"
    if cmd == "query":
        _cmd_query(args)
    elif cmd == "index":
        _cmd_index(args)
    elif cmd == "update":
        _cmd_update(args)
    elif cmd == "rebuild":
        _cmd_rebuild(args)
    else:
        parser.print_help()
        raise SystemExit(1)


def _ensure_default_subcommand() -> None:
    """If the first non-flag arg is not a known subcommand, insert 'query'."""
    if len(sys.argv) < 2:
        return
    first = sys.argv[1]
    if first not in SUBCOMMANDS and not first.startswith("-"):
        sys.argv.insert(1, "query")


# -- subcommand handlers ------------------------------------------------------


def _cmd_query(args: argparse.Namespace) -> None:
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


def _cmd_index(args: argparse.Namespace) -> None:
    _build_and_save_index(Path(args.repo))


def _cmd_rebuild(args: argparse.Namespace) -> None:
    import shutil

    repo = Path(args.repo).resolve()
    emb_dir = repo / DEFAULT_EMBEDDING_CONFIG.index_dir / "embeddings"
    if emb_dir.exists():
        shutil.rmtree(emb_dir)
        logger.info("Removed existing index at %s", emb_dir)
    _build_and_save_index(repo)


def _cmd_update(args: argparse.Namespace) -> None:
    try:
        from repoctx.embeddings import update_file_in_index
    except ImportError:
        print("Embedding dependencies not installed. Run: pip install 'repoctx-mcp[embeddings]'", file=sys.stderr)
        raise SystemExit(1)
    try:
        update_file_in_index(args.file, repo_root=Path(args.repo))
    except (ImportError, FileNotFoundError) as exc:
        print(f"{exc}", file=sys.stderr)
        raise SystemExit(1)
    print(f"Updated embedding for {args.file}")


# -- helpers -------------------------------------------------------------------


def _build_and_save_index(repo: Path) -> None:
    try:
        from repoctx.embeddings import build_index
    except ImportError:
        print("Embedding dependencies not installed. Run: pip install 'repoctx-mcp[embeddings]'", file=sys.stderr)
        raise SystemExit(1)

    repo = repo.resolve()
    try:
        vec_index = build_index(repo)
    except ImportError as exc:
        print(f"{exc}", file=sys.stderr)
        raise SystemExit(1)
    emb_dir = repo / DEFAULT_EMBEDDING_CONFIG.index_dir / "embeddings"
    vec_index.save(emb_dir)
    print(f"Indexed {len(vec_index)} files → {emb_dir}")


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
    import sys

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


if __name__ == "__main__":
    main()
