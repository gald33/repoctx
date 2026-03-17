import argparse
import json
import logging
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from repoctx.retriever import get_task_context
from repoctx.telemetry import record_repoctx_invocation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local repository intelligence for coding agents")
    parser.add_argument("task", help="Task description to retrieve context for")
    parser.add_argument(
        "--repo",
        default=".",
        help="Repository root to inspect (defaults to current directory)",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--session-id",
        help="Optional session identifier for telemetry correlation",
    )
    parser.add_argument(
        "--task-id",
        help="Optional task identifier for telemetry correlation",
    )
    parser.add_argument(
        "--variant",
        choices=("control", "repoctx"),
        default="repoctx",
        help="Experiment variant label for telemetry",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    started = perf_counter()
    session_id = args.session_id or uuid4().hex
    task_id = args.task_id or uuid4().hex
    try:
        response = get_task_context(task=args.task, repo_root=Path(args.repo))
    except Exception as exc:  # pragma: no cover - exercised in CLI runtime
        _record_telemetry(
            task=args.task,
            repo_root=Path(args.repo),
            session_id=session_id,
            task_id=task_id,
            variant=args.variant,
            response=None,
            output_format=args.format,
            success=False,
            error_type=type(exc).__name__,
            duration_ms=int((perf_counter() - started) * 1000),
        )
        logging.getLogger(__name__).error("repoctx failed: %s", exc)
        raise SystemExit(1) from exc

    _record_telemetry(
        task=args.task,
        repo_root=Path(args.repo),
        session_id=session_id,
        task_id=task_id,
        variant=args.variant,
        response=response,
        output_format=args.format,
        success=True,
        error_type=None,
        duration_ms=int((perf_counter() - started) * 1000),
    )

    if args.format == "json":
        print(json.dumps(response.to_dict(), indent=2))
        return

    print(response.context_markdown)


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
    except Exception:  # pragma: no cover - telemetry failures should never break CLI usage
        logger = logging.getLogger(__name__)
        if logger.isEnabledFor(logging.DEBUG):
            logger.warning("Failed to record telemetry", exc_info=True)


if __name__ == "__main__":
    main()
