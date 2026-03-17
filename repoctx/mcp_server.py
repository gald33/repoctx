import argparse
import json
import logging
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from repoctx.retriever import get_task_context as repo_get_task_context
from repoctx.telemetry import record_repoctx_invocation

logger = logging.getLogger(__name__)

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover - exercised in runtime environments without MCP installed
    FastMCP = None


def create_server(repo_root: str | Path | None = None, telemetry_dir: str | Path | None = None):
    if FastMCP is None:
        raise RuntimeError("The 'mcp' package is required to run the MCP server.")

    resolved_root = Path(repo_root).resolve() if repo_root is not None else Path.cwd().resolve()
    server = FastMCP("repoctx")

    @server.tool()
    def get_task_context(task: str) -> dict[str, object]:
        logger.info("Building context for task '%s' in %s", task, resolved_root)
        started = perf_counter()
        session_id = uuid4().hex
        task_id = uuid4().hex
        try:
            response = repo_get_task_context(task=task, repo_root=resolved_root)
        except Exception as exc:
            _record_mcp_telemetry(
                telemetry_dir=telemetry_dir,
                task=task,
                repo_root=resolved_root,
                session_id=session_id,
                task_id=task_id,
                response=None,
                success=False,
                error_type=type(exc).__name__,
                duration_ms=int((perf_counter() - started) * 1000),
            )
            raise

        _record_mcp_telemetry(
            telemetry_dir=telemetry_dir,
            task=task,
            repo_root=resolved_root,
            session_id=session_id,
            task_id=task_id,
            response=response,
            success=True,
            error_type=None,
            duration_ms=int((perf_counter() - started) * 1000),
        )
        return response.to_dict()

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the RepoCtx MCP server")
    parser.add_argument(
        "--repo",
        default=".",
        help="Repository root to inspect (defaults to current working directory)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    create_server(repo_root=args.repo).run()


def _record_mcp_telemetry(
    *,
    telemetry_dir: str | Path | None,
    task: str,
    repo_root: Path,
    session_id: str,
    task_id: str,
    response,
    success: bool,
    error_type: str | None,
    duration_ms: int,
) -> None:
    metrics = response.metrics if response is not None else None
    output_bytes = 0
    if response is not None:
        output_bytes = len(json.dumps(response.to_dict()).encode("utf-8"))

    try:
        record_repoctx_invocation(
            telemetry_dir=telemetry_dir,
            session_id=session_id,
            task_id=task_id,
            variant="repoctx",
            surface="mcp",
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
            output_format="json",
            output_bytes=output_bytes,
        )
    except Exception:
        logger.debug("Failed to record MCP telemetry", exc_info=True)


if __name__ == "__main__":
    main()
