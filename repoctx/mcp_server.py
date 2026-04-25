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


def _recent_repos_path() -> Path:
    """Location of the multi-entry recent-repos log.

    Honors ``REPOCTX_CACHE_DIR`` for tests; otherwise uses ``$XDG_CACHE_HOME``
    or ``~/.cache``. The file is a JSON list of ``{path, last_used}`` entries
    ordered most-recent-first. Used only to *suggest* repos in error messages
    when resolution fails — never auto-selected, because in multi-repo
    workflows "the most recently used repo" is the wrong answer.
    """
    override = os.environ.get("REPOCTX_CACHE_DIR")
    if override:
        return Path(override) / "recent_repos.json"
    base = os.environ.get("XDG_CACHE_HOME")
    if base:
        return Path(base) / "repoctx" / "recent_repos.json"
    return Path.home() / ".cache" / "repoctx" / "recent_repos.json"


_RECENT_REPOS_LIMIT = 10


def _read_recent_repos() -> list[Path]:
    """Return recent repos in most-recent-first order, filtered to live ones."""
    try:
        raw = _recent_repos_path().read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[Path] = []
    for entry in data:
        if isinstance(entry, dict):
            path_str = entry.get("path")
        elif isinstance(entry, str):  # tolerate older single-string format
            path_str = entry
        else:
            continue
        if not isinstance(path_str, str) or not path_str:
            continue
        candidate = Path(path_str)
        if not candidate.is_absolute():
            continue
        # Filter to repos that still exist; surfacing dead paths in error
        # messages is just noise.
        if not (candidate / ".git").exists():
            continue
        out.append(candidate)
    return out


def _record_recent_repo(repo_root: Path) -> None:
    """Add (or move-to-front) ``repo_root`` in the recency log.

    Best-effort: write failures are swallowed.
    """
    try:
        path = _recent_repos_path()
        existing = _read_recent_repos()
        # Move-to-front semantics — most recently resolved wins the top slot.
        deduped = [p for p in existing if p != repo_root]
        merged = [repo_root, *deduped][:_RECENT_REPOS_LIMIT]
        from time import time as _now

        payload = json.dumps(
            [{"path": str(p), "last_used": _now()} for p in merged],
            indent=2,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)
    except OSError:
        logger.debug("Could not persist recent-repos log", exc_info=True)


def resolve_repo_root(explicit: str | Path | None = None) -> Path:
    """Resolve the repo root to inspect.

    Resolution order (only "live" signals — never the recency log, which is
    inherently ambiguous for users with multiple repos):

    1. ``explicit`` (``--repo`` or per-tool ``repo_root`` arg) — strongest.
    2. ``REPOCTX_REPO_ROOT`` env var.
    3. Other host workspace env vars (``CLAUDE_PROJECT_DIR`` etc.).
    4. ``Path.cwd()`` if it isn't ``/`` (launchd subprocesses inherit ``/``).
    5. ``$PWD`` if cwd was ``/`` and ``$PWD`` points somewhere real.

    On failure, the error message lists the most recently resolved repos so
    the model/user can pick one and pass it as ``repo_root``. The recency log
    is also updated on every successful resolution. Within a single MCP
    server process, callers should additionally memoize the first resolved
    root and reuse it (see ``create_server`` — this avoids re-walking the
    chain per tool call and prevents cross-repo drift mid-session).
    """
    candidate, source = _pick_candidate(explicit)
    candidate = candidate.resolve()
    git_root = _find_git_root(candidate)
    if git_root is None:
        recent = _read_recent_repos()
        recent_hint = ""
        if recent:
            joined = ", ".join(str(p) for p in recent[:5])
            recent_hint = f" Recently resolved repos (pick one): {joined}."
        raise RuntimeError(
            "repoctx could not resolve a git repository. "
            f"Searched upward from {candidate} (via {source}). "
            "Fix by calling the tool with an explicit repo_root argument, "
            "setting REPOCTX_REPO_ROOT, or passing --repo /path/to/repo."
            + recent_hint
        )
    _record_recent_repo(git_root)
    return git_root


def _pick_candidate(explicit: str | Path | None) -> tuple[Path, str]:
    if explicit is not None and str(explicit) not in ("", "."):
        return Path(explicit), "explicit"
    # WORKSPACE_FOLDER_PATHS can contain multiple :-separated paths; take the first.
    for var in _HOST_WORKSPACE_ENV_VARS:
        raw = os.environ.get(var)
        if not raw:
            continue
        first = raw.split(os.pathsep)[0].strip()
        if first:
            return Path(first), f"${var}"
    if explicit is not None:  # e.g. explicit="." from argparse default
        return Path(explicit), "explicit"
    cwd = Path.cwd()
    # Treat "/" as no signal — it's almost certainly a launchd-spawned host
    # with no workspace context. Try $PWD as a last live-signal attempt; if
    # it's also missing, fall through to the error path so the model is told
    # to pass repo_root rather than silently picking the wrong repo.
    if str(cwd) != "/":
        return cwd, "cwd"
    pwd = os.environ.get("PWD", "").strip()
    if pwd and pwd != "/":
        pwd_path = Path(pwd)
        if pwd_path.is_absolute() and pwd_path.exists():
            return pwd_path, "$PWD"
    return cwd, "cwd"


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

    # Resolution is deferred to per-call so the server boots even when launched
    # by a host (Claude Desktop, Cursor) that hasn't yet selected a project.
    # Env vars + cwd that carry the workspace are read at the moment a tool is
    # invoked. The explicit `repo_root` (e.g. --repo) is captured here and
    # threaded through unchanged.
    explicit_repo_root = repo_root
    # Per-process session memo. Once any call resolves a repo (via per-call
    # arg, env, or cwd), every subsequent call in this process reuses it
    # unless the caller explicitly overrides with a different repo_root.
    # This is what makes multi-repo Claude Desktop sessions sane: the model
    # only has to supply repo_root once per server lifetime.
    session_root: Path | None = None

    def _resolve(per_call: str | Path | None = None) -> Path:
        nonlocal session_root
        if per_call is not None and str(per_call) not in ("", "."):
            # Explicit per-call override always re-resolves and *replaces*
            # the session memo. Lets the model switch repos mid-session.
            root = resolve_repo_root(per_call)
            session_root = root
            return root
        if session_root is not None:
            return session_root
        root = resolve_repo_root(explicit_repo_root)
        session_root = root
        return root

    # Try resolving once for startup logging + embedding pre-load. Failure here
    # is non-fatal: the server still starts; per-call resolution will surface
    # actionable errors to the user.
    try:
        resolved_root = _resolve()
        logger.info("repoctx MCP server rooted at %s", resolved_root)
        embedding_retriever = _try_load_embeddings(resolved_root)
    except RuntimeError as exc:
        logger.warning(
            "repoctx MCP server starting without a resolved repo root: %s. "
            "Tool calls will retry resolution at invocation time.",
            exc,
        )
        resolved_root = None
        embedding_retriever = None

    server = FastMCP("repoctx")

    @server.tool()
    def get_task_context(task: str, repo_root: str | None = None) -> dict[str, object]:
        """Build context for a task in the user's repository.

        repo_root: Absolute path to the repo to inspect. REQUIRED on the first
        call of a session unless the host has already supplied workspace
        context (Claude Desktop typically has not). After one successful call
        the path is memoized for the lifetime of this MCP server process, so
        subsequent calls may omit repo_root — but if the user is working in a
        different repo, pass it explicitly to switch.
        """
        repo_root = _resolve(repo_root)
        logger.info("Building context for task '%s' in %s", task, repo_root)
        started = perf_counter()
        session_id = uuid4().hex
        task_id = uuid4().hex

        # (Re)load embeddings if startup couldn't resolve a root.
        nonlocal embedding_retriever
        if embedding_retriever is None:
            embedding_retriever = _try_load_embeddings(repo_root)

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
                repo_root=repo_root,
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
                repo_root=repo_root,
                embedding_scores=embedding_scores,
            )
        except Exception as exc:
            _record_mcp_telemetry(
                telemetry_dir=telemetry_dir,
                task=task,
                repo_root=repo_root,
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
            repo_root=repo_root,
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

    def _run_op(op_name: str, task: str, root: Path, fn):
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
                    repo_root=root,
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
                repo_root=root,
                success=True,
                duration_ms=int((perf_counter() - started) * 1000),
                output_bytes=len(json.dumps(result).encode("utf-8")),
            )
        except Exception:
            logger.debug("Failed to record protocol_op telemetry", exc_info=True)
        return result

    # Shared `repo_root` contract for every protocol op. Hosts like Claude
    # Desktop launch the MCP server from / with no workspace env vars, so the
    # model is the only reliable source for the path on the first call.
    _REPO_ROOT_DOC = (
        "repo_root: Absolute path to the repo. REQUIRED on the first call of "
        "a session unless the host already supplied workspace context "
        "(Claude Desktop typically has not). Memoized for the lifetime of "
        "this MCP server process — subsequent calls may omit it. Pass "
        "explicitly to switch repos mid-session."
    )

    def _register(summary: str, fn):
        # Set __doc__ before FastMCP captures it via the decorator.
        fn.__doc__ = f"{summary}\n\n{_REPO_ROOT_DOC}"
        return server.tool()(fn)

    def bundle(task: str, repo_root: str | None = None) -> dict[str, object]:
        root = _resolve(repo_root)
        return _run_op("bundle", task, root, lambda: op_bundle(task, repo_root=root))
    _register("Build the v2 ground-truth bundle for a task.", bundle)

    def authority(
        task: str, include: str = "summary", repo_root: str | None = None
    ) -> dict[str, object]:
        root = _resolve(repo_root)
        inc = "full" if include == "full" else "summary"
        return _run_op("authority", task, root, lambda: op_authority(task, repo_root=root, include=inc))
    _register(
        "List authority records (contracts, constraints) relevant to a task.",
        authority,
    )

    def scope(task: str, repo_root: str | None = None) -> dict[str, object]:
        root = _resolve(repo_root)
        return _run_op("scope", task, root, lambda: op_scope(task, repo_root=root))
    _register("Compute the edit scope (allowed/protected paths) for a task.", scope)

    def validate_plan(
        task: str, changed_files: list[str], repo_root: str | None = None
    ) -> dict[str, object]:
        root = _resolve(repo_root)
        return _run_op(
            "validate_plan",
            task,
            root,
            lambda: op_validate_plan(task, changed_files, repo_root=root),
        )
    _register(
        "Validate a planned change against authority and edit scope.",
        validate_plan,
    )

    def risk_report(
        task: str, changed_files: list[str], repo_root: str | None = None
    ) -> dict[str, object]:
        root = _resolve(repo_root)
        return _run_op(
            "risk_report",
            task,
            root,
            lambda: op_risk_report(task, changed_files, repo_root=root),
        )
    _register("Report risks for a planned change set before finalizing.", risk_report)

    def refresh(
        task: str,
        changed_files: list[str],
        current_scope: dict[str, object] | None = None,
        repo_root: str | None = None,
    ) -> dict[str, object]:
        root = _resolve(repo_root)
        return _run_op(
            "refresh",
            task,
            root,
            lambda: op_refresh(task, changed_files, current_scope, repo_root=root),
        )
    _register("Recompute scope/authority after the change set has shifted.", refresh)

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
            "then $PWD, then the current working directory, then the "
            "last-resolved-repo cache. Each MCP tool also accepts a per-call "
            "repo_root argument."
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
