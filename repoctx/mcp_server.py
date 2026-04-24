import argparse
import json
import logging
import os
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from repoctx.experiment_mcp import mcp_suppression_should_short_circuit
from repoctx.models import ContextMetrics, ContextResponse
from repoctx.retriever import get_task_context as repo_get_task_context
from repoctx.telemetry import record_repoctx_invocation

logger = logging.getLogger(__name__)

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # pragma: no cover - exercised in runtime environments without MCP installed
    FastMCP = None


# Env vars commonly populated by MCP hosts with a notion of "the active workspace".
# Listed in preference order. Checked after an explicit --repo and REPOCTX_REPO_ROOT,
# so hosts that expose workspace context get auto-detection for free.
_HOST_WORKSPACE_ENV_VARS = (
    "REPOCTX_REPO_ROOT",  # explicit override
    "CLAUDE_PROJECT_DIR",  # Claude Code / Claude Desktop
    "WORKSPACE_FOLDER_PATHS",  # Cursor
    "VSCODE_CWD",  # VS Code-derived hosts
)


def resolve_repo_root(explicit: str | Path | None = None) -> Path:
    """Resolve the repo root to inspect.

    Resolution order:

    1. ``explicit`` (the ``--repo`` flag or programmatic override) — strongest.
    2. ``REPOCTX_REPO_ROOT`` env var — for hosts with weak workspace context.
    3. Other host workspace env vars (``CLAUDE_PROJECT_DIR`` etc.) — best-effort.
    4. ``Path.cwd()`` — fallback for CLI-style invocation.

    The chosen candidate is then normalized to the nearest enclosing git root
    (``.git`` directory or file — so worktrees and submodules are not excluded).
    If no git root is found, raises ``RuntimeError`` with an actionable message
    naming both ``--repo`` and ``REPOCTX_REPO_ROOT`` as overrides.
    """
    candidate, source = _pick_candidate(explicit)
    candidate = candidate.resolve()
    git_root = _find_git_root(candidate)
    if git_root is None:
        raise RuntimeError(
            "repoctx could not resolve a git repository. "
            f"Searched upward from {candidate} (via {source}). "
            "Fix by passing --repo /path/to/repo or setting REPOCTX_REPO_ROOT."
        )
    return git_root


def _pick_candidate(explicit: str | Path | None) -> tuple[Path, str]:
    if explicit is not None and str(explicit) not in ("", "."):
        return Path(explicit), "--repo"
    # WORKSPACE_FOLDER_PATHS can contain multiple :-separated paths; take the first.
    for var in _HOST_WORKSPACE_ENV_VARS:
        raw = os.environ.get(var)
        if not raw:
            continue
        first = raw.split(os.pathsep)[0].strip()
        if first:
            return Path(first), f"${var}"
    if explicit is not None:  # e.g. explicit="." from argparse default
        return Path(explicit), "--repo"
    return Path.cwd(), "cwd"


def _find_git_root(start: Path) -> Path | None:
    """Walk upward from ``start`` until a ``.git`` entry is found.

    Accepts both ``.git`` directories (plain repos) and ``.git`` files
    (linked worktrees, submodules). Returns the repo root, or ``None`` if
    none is found before reaching the filesystem root.
    """
    current = start if start.is_dir() else start.parent
    for path in (current, *current.parents):
        if (path / ".git").exists():
            return path
    return None


def create_server(repo_root: str | Path | None = None, telemetry_dir: str | Path | None = None):
    if FastMCP is None:
        raise RuntimeError("The 'mcp' package is required to run the MCP server.")

    resolved_root = resolve_repo_root(repo_root)
    logger.info("repoctx MCP server rooted at %s", resolved_root)
    server = FastMCP("repoctx")

    embedding_retriever = _try_load_embeddings(resolved_root)

    @server.tool()
    def get_task_context(task: str) -> dict[str, object]:
        logger.info("Building context for task '%s' in %s", task, resolved_root)
        started = perf_counter()
        session_id = uuid4().hex
        task_id = uuid4().hex

        if mcp_suppression_should_short_circuit(telemetry_dir=telemetry_dir):
            stub = ContextResponse(
                summary="RepoCtx MCP suppressed for experiment control lane.",
                relevant_docs=[],
                relevant_files=[],
                related_tests=[],
                graph_neighbors=[],
                context_markdown=(
                    "RepoCtx MCP is temporarily suppressed for a control-lane experiment.\n\n"
                    "Tools return an empty stub until the idle TTL passes, a lane is recorded, "
                    "or the treatment lane starts. Run any `repoctx` CLI command to extend the window. "
                    "See ~/.repoctx/config.json (experiment_mcp_* keys)."
                ),
                metrics=ContextMetrics(),
            )
            payload = stub.to_dict(include_metrics=True)
            payload["experiment_mcp_suppressed"] = True
            _record_mcp_telemetry(
                telemetry_dir=telemetry_dir,
                task=task,
                repo_root=resolved_root,
                session_id=session_id,
                task_id=task_id,
                response=None,
                success=False,
                error_type="ExperimentMcpSuppressed",
                duration_ms=int((perf_counter() - started) * 1000),
            )
            return payload

        embedding_scores: dict[str, float] | None = None
        if embedding_retriever is not None:
            try:
                embedding_scores = embedding_retriever.query_scores(task)
            except Exception:
                logger.debug("Embedding scoring failed, continuing with heuristic only", exc_info=True)

        try:
            response = repo_get_task_context(
                task=task,
                repo_root=resolved_root,
                embedding_scores=embedding_scores,
            )
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

    # ---- repoctx v2 protocol ops ------------------------------------------------
    # See docs/plans/2026-04-23-repoctx-v2-design.md § 4.
    from repoctx.protocol import (
        op_authority,
        op_bundle,
        op_refresh,
        op_risk_report,
        op_scope,
        op_validate_plan,
    )
    from repoctx.telemetry import record_protocol_op

    def _run_op(op_name: str, task: str, fn):
        started = perf_counter()
        sess = uuid4().hex
        tid = uuid4().hex
        try:
            result = fn()
        except Exception as exc:
            try:
                record_protocol_op(
                    telemetry_dir=telemetry_dir,
                    op=op_name,
                    surface="mcp",
                    session_id=sess,
                    task_id=tid,
                    task=task,
                    repo_root=resolved_root,
                    success=False,
                    duration_ms=int((perf_counter() - started) * 1000),
                    output_bytes=0,
                    error_type=type(exc).__name__,
                )
            except Exception:
                logger.debug("Failed to record protocol_op telemetry", exc_info=True)
            raise
        try:
            record_protocol_op(
                telemetry_dir=telemetry_dir,
                op=op_name,
                surface="mcp",
                session_id=sess,
                task_id=tid,
                task=task,
                repo_root=resolved_root,
                success=True,
                duration_ms=int((perf_counter() - started) * 1000),
                output_bytes=len(json.dumps(result).encode("utf-8")),
            )
        except Exception:
            logger.debug("Failed to record protocol_op telemetry", exc_info=True)
        return result

    @server.tool()
    def bundle(task: str) -> dict[str, object]:
        return _run_op("bundle", task, lambda: op_bundle(task, repo_root=resolved_root))

    @server.tool()
    def authority(task: str, include: str = "summary") -> dict[str, object]:
        inc = "full" if include == "full" else "summary"
        return _run_op("authority", task, lambda: op_authority(task, repo_root=resolved_root, include=inc))

    @server.tool()
    def scope(task: str) -> dict[str, object]:
        return _run_op("scope", task, lambda: op_scope(task, repo_root=resolved_root))

    @server.tool()
    def validate_plan(task: str, changed_files: list[str]) -> dict[str, object]:
        return _run_op(
            "validate_plan",
            task,
            lambda: op_validate_plan(task, changed_files, repo_root=resolved_root),
        )

    @server.tool()
    def risk_report(task: str, changed_files: list[str]) -> dict[str, object]:
        return _run_op(
            "risk_report",
            task,
            lambda: op_risk_report(task, changed_files, repo_root=resolved_root),
        )

    @server.tool()
    def refresh(
        task: str,
        changed_files: list[str],
        current_scope: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return _run_op(
            "refresh",
            task,
            lambda: op_refresh(task, changed_files, current_scope, repo_root=resolved_root),
        )

    return server


def _try_load_embeddings(repo_root: Path):
    """Best-effort load of embedding retriever at server start."""
    try:
        from repoctx.embeddings import try_load_retriever

        retriever = try_load_retriever(repo_root)
        if retriever is not None:
            logger.info("Embedding retriever loaded for %s", repo_root)
        return retriever
    except Exception:
        logger.debug("Embeddings not available for MCP server", exc_info=True)
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the RepoCtx MCP server")
    parser.add_argument(
        "--repo",
        default=None,
        help=(
            "Repository root to inspect. If omitted, repoctx auto-resolves by "
            "checking REPOCTX_REPO_ROOT, then common host workspace env vars, "
            "then walking up from the current working directory to the nearest "
            ".git root."
        ),
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
