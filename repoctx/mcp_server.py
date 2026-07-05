import argparse
import json
import logging
import os
import threading
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from repoctx.experiment_mcp import mcp_suppression_should_short_circuit
from repoctx.index_consent import (
    attach_consent_metadata,
    prompt_will_be_shown,
    read_consent,
    set_consent,
)
from repoctx.models import ContextMetrics, ContextResponse
from repoctx.retriever import get_task_context as repo_get_task_context
from repoctx.telemetry import (
    record_index_build,
    record_index_consent_event,
    record_repoctx_invocation,
)

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


def _identity_key(repo_root: Path) -> str:
    """Stable per-repo identity used to collapse worktrees to one entry.

    All worktrees of a repo share one git common dir, so keying on it means a
    repo appears once in the recency log regardless of which worktree (or the
    main checkout) invoked repoctx. Falls back to the resolved path when not a
    git repo.
    """
    try:
        from repoctx.git_state import git_common_dir

        common = git_common_dir(repo_root)
        if common is not None:
            return str(common)
    except Exception:  # noqa: BLE001 — identity is best-effort
        pass
    return str(Path(repo_root).resolve())


def _read_recent_repos() -> list[Path]:
    """Return recent repos in most-recent-first order, filtered to live ones.

    Deduped by repo *identity* (git common dir), so multiple worktrees of the
    same repo collapse to a single suggestion.
    """
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
    seen: set[str] = set()
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
        key = _identity_key(candidate)
        if key in seen:
            continue
        seen.add(key)
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
        # Dedupe by identity so a different worktree of the same repo replaces
        # (rather than duplicates) the prior entry.
        new_key = _identity_key(repo_root)
        deduped = [p for p in existing if _identity_key(p) != new_key]
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
    is also updated on every successful resolution. Within a single MCP server
    process, ``create_server`` memoizes the last resolved root and reuses it
    only when a call carries no live signal (Claude Desktop's cwd=``/`` case);
    a live workspace signal or an explicit ``repo_root`` re-resolves, so a
    mid-session repo switch is never masked by a stale memo.
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


def _live_candidate(explicit: str | Path | None) -> tuple[Path, str] | None:
    """The first *live* repo-root signal, or ``None``.

    "Live" signals, in priority order: an explicit ``--repo`` / per-call path,
    a host workspace env var (``REPOCTX_REPO_ROOT``, ``CLAUDE_PROJECT_DIR``, …),
    a real working directory (anything but ``/``), then ``$PWD``. The recency
    log is deliberately excluded — it reflects *past* repos, not where the
    caller is now, so it must never override a live signal or a session memo.

    Returns ``(path, source)`` or ``None`` when nothing live points anywhere —
    the normal case for launchd-spawned hosts (Claude Desktop: cwd ``/``, no
    workspace env), where the session memo or an explicit arg supplies the root.
    """
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
    cwd = Path.cwd()
    # Treat "/" as no signal — it's almost certainly a launchd-spawned host
    # with no workspace context. Try $PWD as a last live-signal attempt.
    if str(cwd) != "/":
        return cwd, "cwd"
    pwd = os.environ.get("PWD", "").strip()
    if pwd and pwd != "/":
        pwd_path = Path(pwd)
        if pwd_path.is_absolute() and pwd_path.exists():
            return pwd_path, "$PWD"
    return None


def _pick_candidate(explicit: str | Path | None) -> tuple[Path, str]:
    live = _live_candidate(explicit)
    if live is not None:
        return live
    if explicit is not None:  # e.g. explicit="." from argparse default
        return Path(explicit), "explicit"
    # No live signal at all. If the recency log has exactly one live entry,
    # auto-pick it — single-repo users get zero friction. Multi-repo users
    # have >1 live entry and fall through to the error path, where they're
    # asked to pick. The "live" filter (.git still exists) protects against
    # stale entries.
    recent = _read_recent_repos()
    if len(recent) == 1:
        return recent[0], "recent (sole entry)"
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
        # No per-call arg. Prefer a *live* signal (host workspace env, a real
        # cwd/$PWD, or an explicit --repo binding) over the session memo, so a
        # mid-session repo switch is honored instead of silently serving a
        # previously-memoized repo (the cross-repo bleed that surfaces another
        # project's files in bundle/validate_plan). The memo is only the
        # fallback for launchd hosts (Claude Desktop: cwd=/, no env) that carry
        # no live signal — exactly what it was introduced for.
        if _live_candidate(explicit_repo_root) is not None:
            try:
                root = resolve_repo_root(explicit_repo_root)
            except RuntimeError:
                # A live signal exists but doesn't resolve to a repo (e.g. the
                # cwd moved to a non-git dir). Don't clobber a usable memo;
                # only surface the error when nothing is memoized.
                if session_root is not None:
                    return session_root
                raise
            session_root = root
            return root
        if session_root is not None:
            return session_root
        root = resolve_repo_root(explicit_repo_root)
        session_root = root
        return root

    # Per-process embedding-retriever cache, keyed to the repo it was loaded
    # for. The retriever wraps a *per-repo* on-disk index, so it must be
    # reloaded when the resolved root changes — reusing a previous repo's
    # retriever would score a new repo's task against the old repo's vectors
    # (cross-repo contamination of retrieval).
    #
    # CRITICAL: the model load behind this (sentence-transformers weights for
    # Qwen3-Embedding-0.6B) can take >60s on a cold CPU host, so it MUST stay
    # off the startup path. Loading it inline before the server serves would
    # stall the MCP `initialize` handshake past the client's ~60s per-request
    # timeout — the connection then fails with "MCP error -32001: Request timed
    # out" and no tools ever register (reproduced reliably in cloud sessions).
    # The load is single-flighted behind a lock + completion event: the
    # background warm-up thread (or the first tool call, whichever runs first)
    # performs it exactly once, and concurrent callers wait on the event instead
    # of kicking off a duplicate load. A non-None retriever is cached; a None
    # result (no index yet, deps missing) is cheap and re-attempted so a
    # mid-session `index` build is picked up.
    embedding_retriever = None
    embedding_retriever_root: Path | None = None
    embed_lock = threading.Lock()
    embed_inflight_root: Path | None = None  # repo whose load is in flight, or None
    embed_done = threading.Event()  # pulsed when an in-flight load finishes

    def _maybe_autoprovision(root: Path) -> None:
        """Kick zero-setup background provisioning of semantic retrieval.

        No-op outside remote containers (env-gated), after the first call for
        a root, and when everything is already live — cheap enough for hot
        paths. Called from ``_ensure_embeddings`` (so the startup warm thread
        triggers it before the first tool call) and from ``_run_op``.
        """
        try:
            from repoctx.autoprovision import maybe_start_auto_provision

            maybe_start_auto_provision(root, telemetry_dir=telemetry_dir)
        except Exception:  # noqa: BLE001 — provisioning must never break serving
            logger.debug("autoprovision kick failed", exc_info=True)

    def _ensure_embeddings(root: Path):
        """Return the embedding retriever for ``root``, loading it at most once.

        Thread-safe and idempotent. The (slow) model load is single-flighted so
        a stampede of first tool calls — and the background warm-up thread —
        collapse to ONE load; concurrent callers wait on ``embed_done`` rather
        than starting their own. A different ``root`` reloads (the retriever
        wraps a per-repo index). Invoked both from the warm-up thread at startup
        and synchronously from the first tool call.
        """
        nonlocal embedding_retriever, embedding_retriever_root, embed_inflight_root
        _maybe_autoprovision(root)
        while True:
            with embed_lock:
                if embedding_retriever is not None and embedding_retriever_root == root:
                    return embedding_retriever
                if embed_inflight_root is None:
                    # Claim the load for this repo.
                    embed_inflight_root = root
                    embed_done.clear()
                    owner = True
                else:
                    # Another load is already running; wait for it to finish,
                    # then re-check (it may have produced what we need).
                    owner = False
            if not owner:
                embed_done.wait()
                continue
            # We own the load. Run it OUTSIDE the lock so the (multi-second)
            # model load never serializes other callers; ``embed_done``
            # coordinates anyone waiting on this same load. The finally clause
            # guarantees the event is pulsed even if the load raises, so a
            # waiter can never hang.
            retriever = None
            try:
                retriever = _try_load_embeddings(root)
            finally:
                with embed_lock:
                    if retriever is not None:
                        embedding_retriever = retriever
                        embedding_retriever_root = root
                    embed_inflight_root = None
                    embed_done.set()
            return retriever

    # Resolve once for startup logging + embedding warm-up. Failure here is
    # non-fatal: the server still starts; per-call resolution surfaces
    # actionable errors at invocation time.
    try:
        resolved_root = _resolve()
        logger.info("repoctx MCP server rooted at %s", resolved_root)
    except RuntimeError as exc:
        logger.warning(
            "repoctx MCP server starting without a resolved repo root: %s. "
            "Tool calls will retry resolution at invocation time.",
            exc,
        )
        resolved_root = None

    # Warm the embedding retriever WITHOUT blocking startup. By default the load
    # runs in a background daemon thread so `initialize` is answered immediately
    # and the first tool call falls back to the synchronous, at-most-once
    # `_ensure_embeddings` path. REPOCTX_EAGER_EMBEDDINGS=1 restores the legacy
    # blocking preload (load on the calling thread before create_server returns)
    # for callers that would rather pay — and surface — the cost up front.
    if resolved_root is not None:
        if _eager_embeddings_enabled():
            _ensure_embeddings(resolved_root)
        else:
            threading.Thread(
                target=_ensure_embeddings,
                args=(resolved_root,),
                name="repoctx-embed-warm",
                daemon=True,
            ).start()

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

        # Load embeddings for the resolved repo. Reloads on a repo switch so a
        # task is never scored against a previously-resolved repo's index. If
        # the background warm-up hasn't finished, this drives the load to
        # completion (at most once) rather than blocking server startup.
        retriever = _ensure_embeddings(repo_root)

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
        if retriever is not None:
            try:
                embedding_scores = retriever.query_scores(task)
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
        return _attach_consent(response.to_dict(), repo_root)

    # ---- repoctx v2 protocol ops ------------------------------------------------
    # See docs/plans/2026-04-23-repoctx-v2-design.md § 4.
    from repoctx.protocol import (
        op_authority,
        op_bundle,
        op_detect_changes,
        op_refresh,
        op_risk_report,
        op_scope,
        op_validate_plan,
    )
    from repoctx.telemetry import record_protocol_op

    def _run_op(op_name: str, task: str, root: Path, fn):
        _maybe_autoprovision(root)
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
        "this MCP server process — subsequent calls may omit it. A live "
        "workspace signal (host env or working directory) or an explicit "
        "repo_root always takes precedence over the memo, so switching repos "
        "mid-session is honored and never silently serves the previous repo."
    )

    def _register(summary: str, fn):
        # Set __doc__ before FastMCP captures it via the decorator.
        fn.__doc__ = f"{summary}\n\n{_REPO_ROOT_DOC}"
        return server.tool()(fn)

    def _attach_consent(payload, root: Path):
        """Wrap a retrieval response with the consent prompt + record telemetry.

        We check ``prompt_will_be_shown`` BEFORE :func:`attach_consent_metadata`
        fires its disk-write side effect, so the recording matches reality
        even though the disk marker is written immediately after. Telemetry
        failures never break the underlying tool call.
        """
        will_show = prompt_will_be_shown(root)
        wrapped = attach_consent_metadata(payload, root)
        if will_show:
            try:
                record_index_consent_event(
                    telemetry_dir=telemetry_dir,
                    session_id=uuid4().hex,
                    surface="mcp",
                    action="prompt_shown",
                    repo_root=root,
                )
            except Exception:
                logger.debug("Failed to record prompt_shown telemetry", exc_info=True)
        return wrapped

    def bundle(
        task: str, repo_root: str | None = None, include_advisory: bool = False
    ) -> dict[str, object]:
        root = _resolve(repo_root)
        result = _run_op(
            "bundle", task, root,
            lambda: op_bundle(task, repo_root=root, include_advisory=include_advisory),
        )
        return _attach_consent(result, root)
    _register(
        "Build the v2 ground-truth bundle for a task. The result carries "
        "top-level `warnings` and a `retrieval` block: if `retrieval.ranker` is "
        "`lexical`/`index_status` isn't `ok`, embedding retrieval is degraded "
        "(see `warnings` for the fix) — do not trust ranking as semantic. Set "
        "`include_advisory=true` to also attach in-flight-branch hits under a "
        "separate `advisory` key (never mixed into `relevant_code`/`authority`).",
        bundle,
    )

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
        result = _run_op("scope", task, root, lambda: op_scope(task, repo_root=root))
        return _attach_consent(result, root)
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
        claude_md_nudge: bool = True,
    ) -> dict[str, object]:
        root = _resolve(repo_root)
        return _run_op(
            "refresh",
            task,
            root,
            lambda: op_refresh(
                task,
                changed_files,
                current_scope,
                repo_root=root,
                claude_md_nudge=claude_md_nudge,
            ),
        )
    _register("Recompute scope/authority after the change set has shifted.", refresh)

    def install(
        repo_root: str | None = None,
        scaffold_authority: bool = True,
        claude_md_nudge: bool = True,
    ) -> dict[str, object]:
        from repoctx.harness import install_all

        root = _resolve(repo_root)
        return _run_op(
            "install",
            "",
            root,
            lambda: install_all(
                repo_root=root,
                scaffold_authority=scaffold_authority,
                claude_md_nudge=claude_md_nudge,
            ),
        )
    _register(
        "Install repoctx into a repo: writes AGENTS.md section + MCP config "
        "for Claude Code / Cursor / Codex, and (by default) scaffolds the "
        "contracts/docs/examples authority layout. Idempotent.",
        install,
    )

    def index(
        repo_root: str | None = None,
        decline: bool = False,
    ) -> dict[str, object]:
        # The single explicit-consent surface for the embedding index. Either
        # branch records the user's answer in <repo>/.repoctx/config.json so
        # the one-shot consent prompt never re-appears.
        root = _resolve(repo_root)
        # Capture `previous_action` before any state flip so telemetry can
        # distinguish "first answer" from "user changed their mind".
        previous = read_consent(root)
        if decline:
            try:
                set_consent(root, "declined")
            except Exception as exc:
                return {
                    "status": "error",
                    "action": "decline",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            try:
                record_index_consent_event(
                    telemetry_dir=telemetry_dir,
                    session_id=uuid4().hex,
                    surface="mcp",
                    action="declined",
                    repo_root=root,
                    previous_action=previous,
                )
            except Exception:
                logger.debug("Failed to record declined telemetry", exc_info=True)
            return {
                "status": "declined",
                "message": (
                    "Recorded: this repo will not be prompted to index again. "
                    "Retrieval will use lexical-only matching. Run `repoctx "
                    "index` from the CLI, or call this tool without "
                    "`decline=true`, to change your mind."
                ),
            }

        from repoctx.harness import _maybe_build_index

        def _build() -> dict[str, object]:
            build_started = perf_counter()
            errors: dict[str, str] = {}
            build_metrics: dict[str, object] = {}
            status = _maybe_build_index(root, True, errors, metrics_out=build_metrics)
            if errors:
                return {"status": "error", "errors": errors}
            if status is None:
                # _maybe_build_index returns None only via errors when
                # build_index=True; this branch is defensive.
                return {
                    "status": "error",
                    "errors": {"embedding_index": "build returned no status"},
                }
            try:
                set_consent(root, "granted")
            except Exception:
                logger.warning("Failed to record granted index consent", exc_info=True)
            try:
                record_index_consent_event(
                    telemetry_dir=telemetry_dir,
                    session_id=uuid4().hex,
                    surface="mcp",
                    action="granted",
                    repo_root=root,
                    previous_action=previous,
                    duration_ms=int((perf_counter() - build_started) * 1000),
                )
            except Exception:
                logger.debug("Failed to record granted telemetry", exc_info=True)
            try:
                record_index_build(
                    telemetry_dir=telemetry_dir,
                    session_id=uuid4().hex,
                    surface="mcp",
                    repo_root=root,
                    success=True,
                    duration_ms=int((perf_counter() - build_started) * 1000),
                    source=str(build_metrics.get("source", "origin-main")),
                    incremental=bool(build_metrics.get("incremental", False)),
                    chunk_count=int(build_metrics.get("chunk_count", 0) or 0),
                    file_count=int(build_metrics.get("file_count", 0) or 0),
                    embedded_chunk_count=int(build_metrics.get("embedded_chunk_count", 0) or 0),
                    model_load_ms=build_metrics.get("model_load_ms"),  # type: ignore[arg-type]
                    embed_ms=build_metrics.get("embed_ms"),  # type: ignore[arg-type]
                    scan_ms=build_metrics.get("scan_ms"),  # type: ignore[arg-type]
                    device=build_metrics.get("device"),  # type: ignore[arg-type]
                    dtype=build_metrics.get("dtype"),  # type: ignore[arg-type]
                    model_name=build_metrics.get("model_name"),  # type: ignore[arg-type]
                )
            except Exception:
                logger.debug("Failed to record index_build telemetry", exc_info=True)
            # _maybe_build_index returns {"status": "built", "files": N, "index_dir": ...}
            return status

        return _run_op("index", "", root, _build)
    _register(
        "Build the embedding index for this repo (downloads the embedding "
        "model on first use and scans every file — see the one-shot "
        "`index_consent_prompt` for cost details). Records the user's "
        "consent so the prompt won't re-appear. Call with `decline=true` "
        "to record that the user does NOT want the index built; this "
        "suppresses the prompt without performing any download or scan.",
        index,
    )

    def stats(
        days: int = 30,
        repo_root: str | None = None,
    ) -> dict[str, object]:
        from repoctx.stats import compute_stats
        from repoctx.telemetry import sha256_hex

        repo_hash = None
        if repo_root:
            repo_hash = sha256_hex(str(Path(repo_root).resolve()))
        window = None if days == 0 else days
        return compute_stats(days=window, repo_hash=repo_hash)
    _register(
        "Aggregate repoctx telemetry: per-op counts, success rate, p50/p95 "
        "latency, daily activity, recent errors. `days=0` means all time. "
        "Pass `repo_root` to scope to a single repo. Read-only — does not "
        "scan the repo.",
        stats,
    )

    def propose_authority(repo_root: str | None = None) -> dict[str, object]:
        from repoctx.authority.propose import propose_authority as _propose

        root = _resolve(repo_root)
        return _run_op(
            "propose_authority",
            "",
            root,
            lambda: _propose(repo_root=root),
        )
    _register(
        "Generate a brief telling YOU (the agent) which contracts and "
        "architecture notes to write for this repo, with detected subsystems, "
        "contract surfaces, and a concrete file checklist. Use the returned "
        "`agent_brief` plus `suggested_files` to author the authority layout "
        "via your Write tool. Run after `install`, before the user's first task.",
        propose_authority,
    )

    def detect_changes(
        changed_files: list[str] | None = None,
        repo_root: str | None = None,
    ) -> dict[str, object]:
        root = _resolve(repo_root)
        files = list(changed_files or [])
        return _run_op(
            "detect_changes",
            "",
            root,
            lambda: op_detect_changes(files, repo_root=root),
        )
    _register(
        "Map changed files to their direct + transitive callers via the import graph. "
        "Defaults to git's dirty file list when changed_files is empty.",
        detect_changes,
    )

    def semantic_search(
        query: str,
        top_k: int = 10,
        kind: str | None = None,
        repo_root: str | None = None,
    ) -> dict[str, object]:
        from repoctx.ops import op_semantic_search

        root = _resolve(repo_root)
        result = _run_op(
            "semantic_search",
            query,
            root,
            lambda: op_semantic_search(
                query, repo_root=root, top_k=top_k, kind=kind,
            ),
        )
        # On the cold-start call against an unindexed repo (status="no_index"),
        # _attach_consent adds the one-shot `index_consent_prompt` and records
        # a `prompt_shown` telemetry event. Subsequent calls — and any call
        # where consent is already recorded — pass the envelope through
        # unchanged (modulo a quiet `index_consent: "declined"` hint if the
        # user previously declined).
        return _attach_consent(result, root)
    _register(
        "Top-K most similar chunks for a query against the embedding index. "
        "Returns an envelope {status, message, repo, index_location, results}: "
        "`results` is the per-chunk hits (path, score, snippet, line range, "
        "enclosing_symbol) sorted by descending cosine similarity; `status` is "
        "`ok` when embedding search ran, else `no_index`/`deps_missing`/"
        "`schema_mismatch`/`error` with a `message` saying how to fix it (e.g. "
        "run `repoctx index`). A status other than `ok` means retrieval is "
        "DARK — do not treat an empty `results` as 'no matches'. `kind` "
        "optionally filters to code/doc/test/config. On the FIRST call against "
        "an unindexed repo the envelope also carries `index_consent_prompt` "
        "asking the user (once) whether to build the index — relay the prompt "
        "verbatim and call the `index` tool with their answer. For task-shaped "
        "retrieval prefer `bundle`.",
        semantic_search,
    )

    def advisory_search(
        query: str,
        top_k: int = 10,
        repo_root: str | None = None,
    ) -> dict[str, object]:
        from repoctx.advisory import op_advisory_search

        root = _resolve(repo_root)
        return _run_op(
            "advisory_search",
            query,
            root,
            lambda: op_advisory_search(query, repo_root=root, top_k=top_k),
        )
    _register(
        "Search the ADVISORY lane: committed work on branches ahead of "
        "origin/main (in-flight, not landed). Use to check whether something "
        "is already being built elsewhere or where the architecture is heading. "
        "Returns hits tagged with provenance (branch, commits_ahead, "
        "last_commit_date, merge_status). These are STRICTLY LOWER AUTHORITY "
        "than `bundle`/`semantic_search` — never treat them as ground truth. "
        "Opt-in: returns status `no_index` until you run `repoctx advisory-index`.",
        advisory_search,
    )

    def mark_used(
        bundle_id: str,
        labels: list[dict[str, object]],
        repo_root: str | None = None,
    ) -> dict[str, object]:
        from repoctx.ops import op_mark_used

        root = _resolve(repo_root)
        return _run_op(
            "mark_used",
            bundle_id,
            root,
            lambda: op_mark_used(bundle_id, labels, repo_root=root),
        )
    _register(
        "Record graded relevance labels for files in a bundle you used. "
        "`labels` is a list of {path, relevance} where relevance is one of "
        "`informed_edit` (you edited this file), `informed_context` (you "
        "read it and it shaped an edit elsewhere — the highest-value signal "
        "no other source can capture), or `noise` (it ended up in the "
        "bundle but didn't earn its slot). Call this at task end so the "
        "Phase 3 tuner can fit per-kind retrieval thresholds. `bundle_id` "
        "is on the bundle response.",
        mark_used,
    )

    @server.tool()
    def reporting(
        action: str = "status",
        limit: int = 10,
        purge: bool = False,
    ) -> dict[str, object]:
        """Inspect or toggle anonymous usage reporting for this install.

        Reporting uploads counts/timings/error-classes (never paths, queries,
        or code) so the maintainer can tune retrieval. Stable builds default
        to OFF — the user (or you, on their behalf) must explicitly enable
        it. Canary builds default to ON with a one-time disclosure.

        action:
          - "status": current channel, enabled state, install_id, queue size.
          - "on": enable reporting.
          - "off": disable reporting. Pass purge=True to also drop queued events.
          - "show": return the up-to `limit` most-recently queued events that
            would be uploaded. Use this to show the user exactly what's sent.
          - "flush": attempt to upload the queue now.

        This tool affects only the local install — it does NOT depend on a
        repo_root and does NOT touch any repo files.
        """
        from repoctx import reporting as reporting_module

        if action == "status":
            return reporting_module.get_status()
        if action == "on":
            reporting_module.set_enabled(True)
            return {"ok": True, **reporting_module.get_status()}
        if action == "off":
            reporting_module.set_enabled(False)
            purged_bytes = reporting_module.purge_queue() if purge else 0
            return {
                "ok": True,
                "purged_bytes": purged_bytes,
                **reporting_module.get_status(),
            }
        if action == "show":
            return {
                "events": reporting_module.get_queued_events(limit=limit),
                **reporting_module.get_status(),
            }
        if action == "flush":
            result = reporting_module.flush()
            return {
                "sent": result.sent,
                "accepted": result.accepted,
                "rejected": result.rejected,
                "error": result.error,
                **reporting_module.get_status(),
            }
        return {
            "ok": False,
            "error": f"unknown action: {action!r}; expected status|on|off|show|flush",
        }

    return server


def _eager_embeddings_enabled() -> bool:
    """Whether to preload embeddings on the startup (calling) thread.

    Default off: the embedding model load (>60s on a cold CPU host) is warmed
    in a background daemon thread so the MCP ``initialize`` handshake is never
    blocked. Set ``REPOCTX_EAGER_EMBEDDINGS=1`` (or ``true``/``yes``/``on``) to
    restore the legacy blocking preload.
    """
    return os.environ.get("REPOCTX_EAGER_EMBEDDINGS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


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

    # No-op on stable; prints a one-time stderr disclosure on canary builds.
    # Goes to stderr, not stdout, so it can't corrupt MCP stdio framing.
    try:
        from repoctx import reporting

        reporting.maybe_show_canary_notice()
    except Exception:  # noqa: BLE001 — disclosure must never break server boot
        pass

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
