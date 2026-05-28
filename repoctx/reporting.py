"""Anonymous usage reporting (upload) for repoctx.

This sits on top of :mod:`repoctx.telemetry` — that module writes a local
JSONL event log used by the tuner; this module is the optional layer that
uploads a redacted subset of those events to a maintainer-run ingest endpoint
(a Cloudflare Worker + D1, in our case).

Privacy contract
----------------

* **Stable channel: OFF by default.** No prompt, no first-run question — the
  user (or an agent acting on their behalf) must explicitly call
  ``set_enabled(True)`` for anything to be sent. This is the trust-preserving
  default.
* **Canary channel: ON by default**, with a one-time disclosure notice
  emitted to stderr on first invocation (so canary users are never surprised
  but also never blocked by a prompt).
* **Env var kill switch:** ``REPOCTX_REPORTING=off`` overrides everything,
  in case a user wants to hard-disable without touching files.
* **Payload contents:** counts, timings, error *classes* (never messages),
  per-op stats. **Never** paths, queries, code, error messages, git remote
  URLs, hostnames. The ingest Worker enforces this independently.
* **Repo identity:** events carry ``repo_fingerprint`` =
  ``sha256(install_id || first_commit_sha)`` — stable per (install, repo)
  but not correlatable across users.

State layout (under ``~/.repoctx/``, override via ``REPOCTX_REPORTING_DIR``)
---------------------------------------------------------------------------

* ``reporting.json`` — install_id, enabled flag, endpoint, notice flags.
* ``reporting/queue.jsonl`` — pending events awaiting upload.
* ``reporting/sent.log`` — append-only audit of successfully sent batches.

Transport
---------

The default :class:`Poster` is :class:`LoggingPoster`, which only records
what *would* be sent (to ``sent.log``) and returns success. Production builds
swap in :class:`HttpPoster` via :func:`get_default_poster`. This makes tests
trivial — no network mocking — and lets a user inspect outgoing payloads with
``repoctx reporting show`` before flipping the real endpoint on.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol

from repoctx._build_channel import BUILD_ID, CHANNEL, Channel

logger = logging.getLogger(__name__)

# ---- Constants --------------------------------------------------------------

UPLOAD_SCHEMA_VERSION = 1

# Cloudflare Worker deployed from ``server/``. Override at runtime via
# REPOCTX_REPORTING_ENDPOINT (useful for staging/local-dev endpoints) or via
# the ``endpoint`` field in ``~/.repoctx/reporting.json``.
DEFAULT_ENDPOINT: str | None = "https://repoctx-reports.repoctx.workers.dev/v1/events"

STATE_FILENAME = "reporting.json"
QUEUE_DIRNAME = "reporting"
QUEUE_FILENAME = "queue.jsonl"
SENT_LOG_FILENAME = "sent.log"

DEFAULT_MAX_QUEUE_BYTES = 1_048_576  # 1 MB
DEFAULT_FLUSH_BATCH_BYTES = 65_536  # 64 KB — opportunistic flush threshold
DEFAULT_HTTP_TIMEOUT_SECONDS = 5.0
ATEXIT_FLUSH_TIMEOUT_SECONDS = 2.0

# Top-level keys we MUST strip when converting a local telemetry payload
# into an upload payload. Defense-in-depth: telemetry.py shouldn't produce
# any of these, but if a future refactor adds one, we still won't leak.
FORBIDDEN_UPLOAD_KEYS = frozenset({
    "query",
    "query_text",
    "query_hash",  # local-only; leaks structure across users
    "task",
    "task_text",
    "task_hash",  # local-only; same reason
    "prompt",
    "prompt_hash",
    "code",
    "content",
    "repo_root",
    "repo_hash",  # replaced by repo_fingerprint at the boundary
    "path",
    "paths",
    "control_worktree",
    "repoctx_worktree",
    "worktree_path",
    "remote",
    "remote_url",
    "git_remote",
    "hostname",
    "username",
    "user",
    "error_message",
    "error_msg",
    "stack_trace",
    "traceback",
})

_CACHED_INSTALL_ID: str | None = None
_ATEXIT_REGISTERED = False


# ---- Channel + env-var helpers ---------------------------------------------


def get_channel() -> Channel:
    """Return the channel this build belongs to (``stable`` or ``canary``)."""
    return CHANNEL


def get_build_id() -> str:
    return BUILD_ID


def _env_kill_switch() -> bool | None:
    """Return ``False`` if ``REPOCTX_REPORTING`` is set to an off-ish value.

    Recognized off values (case-insensitive): ``off``, ``0``, ``false``, ``no``.
    ``on``/``true``/``1``/``yes`` returns True. Anything else returns None
    (meaning "no override; fall through to file/channel default").
    """
    raw = os.environ.get("REPOCTX_REPORTING")
    if raw is None:
        return None
    norm = raw.strip().lower()
    if norm in {"off", "0", "false", "no", "disable", "disabled"}:
        return False
    if norm in {"on", "1", "true", "yes", "enable", "enabled"}:
        return True
    return None


def get_state_dir(state_dir: str | Path | None = None) -> Path:
    """Resolve the directory containing ``reporting.json`` and the queue dir.

    Precedence: explicit arg > ``REPOCTX_REPORTING_DIR`` > ``~/.repoctx``.
    """
    if state_dir is not None:
        return Path(state_dir)
    override = os.environ.get("REPOCTX_REPORTING_DIR")
    if override:
        return Path(override)
    return Path.home() / ".repoctx"


def _state_path(state_dir: Path) -> Path:
    return state_dir / STATE_FILENAME


def _queue_dir(state_dir: Path) -> Path:
    return state_dir / QUEUE_DIRNAME


def _queue_path(state_dir: Path) -> Path:
    return _queue_dir(state_dir) / QUEUE_FILENAME


def _sent_log_path(state_dir: Path) -> Path:
    return _queue_dir(state_dir) / SENT_LOG_FILENAME


# ---- State (reporting.json) -------------------------------------------------


@dataclass
class ReportingState:
    install_id: str
    enabled: bool | None  # None = use channel default
    endpoint: str | None
    canary_notice_shown: bool
    max_queue_bytes: int
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "install_id": self.install_id,
            "enabled": self.enabled,
            "endpoint": self.endpoint,
            "canary_notice_shown": self.canary_notice_shown,
            "max_queue_bytes": self.max_queue_bytes,
        }


def _channel_default_enabled() -> bool:
    return CHANNEL == "canary"


def _new_state() -> ReportingState:
    return ReportingState(
        install_id=str(uuid.uuid4()),
        enabled=None,
        endpoint=None,
        canary_notice_shown=False,
        max_queue_bytes=DEFAULT_MAX_QUEUE_BYTES,
    )


def _read_state_if_exists(state_dir: str | Path | None = None) -> ReportingState | None:
    """Read the state file. Returns None if absent — no side effects.

    Used by read-only paths (``is_enabled``, ``get_endpoint``) so that
    merely *checking* whether reporting is on doesn't materialize a state
    file. Stable installs that never opt in stay file-free.
    """
    dir_path = get_state_dir(state_dir)
    path = _state_path(dir_path)
    if not path.exists():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("repoctx reporting state at %s is unreadable (%s); resetting", path, exc)
        return None

    if not isinstance(payload, dict):
        logger.warning("repoctx reporting state at %s is not an object; resetting", path)
        return None

    install_id = payload.get("install_id")
    if not isinstance(install_id, str) or not install_id:
        install_id = str(uuid.uuid4())

    enabled = payload.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        enabled = None

    endpoint = payload.get("endpoint")
    if endpoint is not None and not isinstance(endpoint, str):
        endpoint = None

    notice = payload.get("canary_notice_shown")
    if not isinstance(notice, bool):
        notice = False

    max_queue = payload.get("max_queue_bytes")
    if not isinstance(max_queue, int) or max_queue <= 0:
        max_queue = DEFAULT_MAX_QUEUE_BYTES

    return ReportingState(
        install_id=install_id,
        enabled=enabled,
        endpoint=endpoint,
        canary_notice_shown=notice,
        max_queue_bytes=max_queue,
    )


def load_state(state_dir: str | Path | None = None) -> ReportingState:
    """Read the state file, creating it with defaults if absent.

    Use for write paths (set_enabled, enqueue, disclosure flag) where we
    actually intend to persist. For read-only checks use
    :func:`_read_state_if_exists` so a stable install that never opts in
    doesn't materialize ``~/.repoctx/reporting.json``.

    Never raises on a malformed file — falls back to a fresh state with a
    new install_id. We'd rather lose the old install_id than crash a CLI
    invocation on a corrupted JSON.
    """
    state = _read_state_if_exists(state_dir)
    if state is not None:
        return state
    state = _new_state()
    save_state(state, state_dir=state_dir)
    return state


def save_state(state: ReportingState, *, state_dir: str | Path | None = None) -> Path:
    dir_path = get_state_dir(state_dir)
    dir_path.mkdir(parents=True, exist_ok=True)
    path = _state_path(dir_path)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


# ---- Public knobs -----------------------------------------------------------


def is_enabled(state_dir: str | Path | None = None) -> bool:
    """Resolve the effective enabled state, applying all precedence rules.

    Precedence (highest wins):
      1. ``REPOCTX_REPORTING`` env var
      2. ``reporting.json`` ``enabled`` field (if explicitly set)
      3. Channel default (canary=True, stable=False)

    Side-effect-free: a stable install that never opts in never causes the
    state file to be created.
    """
    env = _env_kill_switch()
    if env is not None:
        return env
    state = _read_state_if_exists(state_dir)
    if state is not None and state.enabled is not None:
        return state.enabled
    return _channel_default_enabled()


def set_enabled(value: bool, *, state_dir: str | Path | None = None) -> ReportingState:
    state = load_state(state_dir)
    state.enabled = value
    save_state(state, state_dir=state_dir)
    return state


def get_install_id(state_dir: str | Path | None = None) -> str:
    """Cached install_id lookup.

    Caches across calls within a process for speed. Tests that switch
    ``state_dir`` mid-process should call :func:`_reset_install_id_cache`.
    """
    global _CACHED_INSTALL_ID
    if _CACHED_INSTALL_ID is not None and state_dir is None and "REPOCTX_REPORTING_DIR" not in os.environ:
        return _CACHED_INSTALL_ID
    state = load_state(state_dir)
    if state_dir is None and "REPOCTX_REPORTING_DIR" not in os.environ:
        _CACHED_INSTALL_ID = state.install_id
    return state.install_id


def _reset_install_id_cache() -> None:
    global _CACHED_INSTALL_ID
    _CACHED_INSTALL_ID = None


def get_endpoint(state_dir: str | Path | None = None) -> str | None:
    """Resolve the upload endpoint.

    Precedence: ``REPOCTX_REPORTING_ENDPOINT`` env > state file > module
    default. None means "no endpoint configured" — uploads degrade to the
    :class:`LoggingPoster` which only writes to ``sent.log``.

    Side-effect-free: doesn't create the state file if absent.
    """
    env = os.environ.get("REPOCTX_REPORTING_ENDPOINT")
    if env:
        return env
    state = _read_state_if_exists(state_dir)
    if state is not None and state.endpoint:
        return state.endpoint
    return DEFAULT_ENDPOINT


def get_status(state_dir: str | Path | None = None) -> dict[str, Any]:
    """One-call summary for the CLI ``status`` action and the MCP tool."""
    state = load_state(state_dir)
    env = _env_kill_switch()
    effective = is_enabled(state_dir)
    queue_size = _queue_size_bytes(get_state_dir(state_dir))
    return {
        "channel": CHANNEL,
        "build_id": BUILD_ID,
        "install_id": state.install_id,
        "enabled": effective,
        "enabled_source": (
            "env" if env is not None
            else "state_file" if state.enabled is not None
            else "channel_default"
        ),
        "state_file_value": state.enabled,
        "channel_default": _channel_default_enabled(),
        "endpoint": get_endpoint(state_dir),
        "queue_bytes": queue_size,
        "queue_path": str(_queue_path(get_state_dir(state_dir))),
    }


# ---- Repo fingerprint -------------------------------------------------------


def _git_first_commit_sha(repo_root: str | Path) -> str | None:
    """Return the SHA of the very first commit on the repo (root-commit).

    Uses ``git rev-list --max-parents=0 HEAD`` — finds commits with no
    parents (typically just the initial commit). Returns the first one for
    stability across rebases/squashes that don't touch the root. Returns
    None if the directory isn't a git repo or git isn't available.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-list", "--max-parents=0", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    line = result.stdout.strip().splitlines()
    if not line:
        return None
    return line[0].strip() or None


def compute_repo_fingerprint(
    repo_root: str | Path | None,
    *,
    state_dir: str | Path | None = None,
) -> str | None:
    """``sha256(install_id || first_commit_sha)`` — stable per (install, repo).

    Returns None if the path isn't a git repo (no first-commit SHA to
    anchor on). The install_id mixed in means two users with the same repo
    produce different fingerprints, so the value can't be precomputed and
    used to identify "which open-source repo is this."
    """
    if repo_root is None:
        return None
    first = _git_first_commit_sha(repo_root)
    if first is None:
        return None
    install_id = get_install_id(state_dir)
    return hashlib.sha256(f"{install_id}|{first}".encode("utf-8")).hexdigest()


# ---- Payload construction ---------------------------------------------------


def build_upload_payload(
    local_event: dict[str, Any],
    *,
    repo_root: str | Path | None = None,
    state_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Convert a local telemetry payload into an upload-safe payload.

    Strips forbidden keys, attaches channel/build_id/install_id, replaces
    any local repo_hash with repo_fingerprint.
    """
    out: dict[str, Any] = {}
    for key, value in local_event.items():
        if key in FORBIDDEN_UPLOAD_KEYS:
            continue
        out[key] = value

    out["upload_schema_version"] = UPLOAD_SCHEMA_VERSION
    out["channel"] = CHANNEL
    out["build_id"] = BUILD_ID
    out["install_id"] = get_install_id(state_dir)

    fp = compute_repo_fingerprint(repo_root, state_dir=state_dir)
    if fp is not None:
        out["repo_fingerprint"] = fp

    return out


# ---- Queue ------------------------------------------------------------------


def _queue_size_bytes(state_dir: Path) -> int:
    path = _queue_path(state_dir)
    if not path.exists():
        return 0
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _truncate_queue_to_fit(state_dir: Path, max_bytes: int) -> int:
    """Drop oldest events until the queue fits under ``max_bytes``.

    Returns the number of events dropped.
    """
    path = _queue_path(state_dir)
    if not path.exists():
        return 0
    try:
        size = path.stat().st_size
    except OSError:
        return 0
    if size <= max_bytes:
        return 0

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0

    dropped = 0
    while lines:
        joined = ("\n".join(lines) + "\n").encode("utf-8")
        if len(joined) <= max_bytes:
            break
        lines.pop(0)
        dropped += 1

    if not lines:
        path.unlink(missing_ok=True)
    else:
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.replace(path)

    return dropped


def enqueue_if_enabled(
    local_event: dict[str, Any],
    *,
    repo_root: str | Path | None = None,
    state_dir: str | Path | None = None,
) -> bool:
    """Enqueue an event for upload, iff reporting is on.

    Returns True if the event was queued, False if reporting is disabled or
    the event was rejected by client-side validation. Safe to call from
    inside telemetry recorders — never raises, never blocks on network.
    """
    if not is_enabled(state_dir):
        return False

    try:
        payload = build_upload_payload(
            local_event,
            repo_root=repo_root,
            state_dir=state_dir,
        )
    except Exception as exc:  # noqa: BLE001 — defensive; never break the caller
        logger.debug("reporting: failed to build upload payload: %s", exc)
        return False

    dir_path = get_state_dir(state_dir)
    queue_dir = _queue_dir(dir_path)
    try:
        queue_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.debug("reporting: cannot create queue dir %s: %s", queue_dir, exc)
        return False

    line = json.dumps(payload, sort_keys=True) + "\n"
    queue_path = _queue_path(dir_path)
    try:
        # Append mode + single write call is atomic for sizes under PIPE_BUF
        # (typically 4 KB), which our events comfortably fit in.
        with queue_path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError as exc:
        logger.debug("reporting: cannot write to queue %s: %s", queue_path, exc)
        return False

    state = load_state(state_dir)
    _truncate_queue_to_fit(dir_path, state.max_queue_bytes)

    _ensure_atexit_flush(state_dir)
    return True


def get_queued_events(
    limit: int = 10,
    *,
    state_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Read up to ``limit`` queued events for inspection. Most-recent last."""
    dir_path = get_state_dir(state_dir)
    path = _queue_path(dir_path)
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for raw in lines[-limit:]:
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return out


def purge_queue(state_dir: str | Path | None = None) -> int:
    """Delete the queue file. Returns the byte count that was dropped."""
    dir_path = get_state_dir(state_dir)
    path = _queue_path(dir_path)
    if not path.exists():
        return 0
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    path.unlink(missing_ok=True)
    return size


# ---- Posters (transport) ----------------------------------------------------


@dataclass
class PostResult:
    sent: int
    accepted: int | None  # None if the poster doesn't track this
    rejected: int | None
    error: str | None  # None on success


class Poster(Protocol):
    def post(self, events: list[dict[str, Any]]) -> PostResult: ...


class LoggingPoster:
    """Default Poster: writes to ``sent.log`` instead of actually sending.

    Used when no endpoint is configured (or in tests). Lets the user run
    ``repoctx reporting show`` to inspect what *would* be uploaded.
    """

    def __init__(self, state_dir: str | Path | None = None) -> None:
        self._state_dir = state_dir

    def post(self, events: list[dict[str, Any]]) -> PostResult:
        dir_path = get_state_dir(self._state_dir)
        sent_log = _sent_log_path(dir_path)
        try:
            sent_log.parent.mkdir(parents=True, exist_ok=True)
            with sent_log.open("a", encoding="utf-8") as handle:
                for event in events:
                    handle.write(json.dumps(event, sort_keys=True) + "\n")
        except OSError as exc:
            return PostResult(
                sent=0, accepted=None, rejected=None, error=f"local_write_failed:{exc}"
            )
        return PostResult(sent=len(events), accepted=len(events), rejected=0, error=None)


class HttpPoster:
    """POSTs NDJSON to the configured endpoint using stdlib urllib."""

    def __init__(
        self,
        endpoint: str,
        *,
        timeout: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
        state_dir: str | Path | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._timeout = timeout
        self._state_dir = state_dir

    def post(self, events: list[dict[str, Any]]) -> PostResult:
        if not events:
            return PostResult(sent=0, accepted=0, rejected=0, error=None)

        body = ("\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n").encode("utf-8")
        request = urllib.request.Request(
            self._endpoint,
            data=body,
            method="POST",
            headers={
                "content-type": "application/x-ndjson",
                "user-agent": f"repoctx/{BUILD_ID} ({CHANNEL})",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                status = response.getcode()
        except urllib.error.HTTPError as exc:
            return PostResult(
                sent=0,
                accepted=None,
                rejected=None,
                error=f"http_{exc.code}",
            )
        except urllib.error.URLError as exc:
            return PostResult(sent=0, accepted=None, rejected=None, error=f"url_error:{exc.reason}")
        except (OSError, TimeoutError) as exc:
            return PostResult(sent=0, accepted=None, rejected=None, error=f"transport:{exc}")

        if status >= 300:
            return PostResult(sent=0, accepted=None, rejected=None, error=f"http_{status}")

        accepted: int | None = None
        rejected: int | None = None
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                accepted = parsed.get("accepted") if isinstance(parsed.get("accepted"), int) else None
                rejected = parsed.get("rejected") if isinstance(parsed.get("rejected"), int) else None
        except json.JSONDecodeError:
            pass

        # Mirror what we sent to the audit log so the user can always inspect.
        try:
            dir_path = get_state_dir(self._state_dir)
            sent_log = _sent_log_path(dir_path)
            sent_log.parent.mkdir(parents=True, exist_ok=True)
            with sent_log.open("a", encoding="utf-8") as handle:
                for event in events:
                    handle.write(json.dumps(event, sort_keys=True) + "\n")
        except OSError:
            pass  # audit log is best-effort; don't fail the send on it

        return PostResult(
            sent=len(events),
            accepted=accepted if accepted is not None else len(events),
            rejected=rejected if rejected is not None else 0,
            error=None,
        )


def get_default_poster(state_dir: str | Path | None = None) -> Poster:
    endpoint = get_endpoint(state_dir)
    if endpoint:
        return HttpPoster(endpoint, state_dir=state_dir)
    return LoggingPoster(state_dir=state_dir)


# ---- Flush ------------------------------------------------------------------


def flush(
    *,
    poster: Poster | None = None,
    state_dir: str | Path | None = None,
) -> PostResult:
    """Drain the queue. On success, queue is cleared; on failure, kept.

    Safe to call when reporting is disabled — returns a zero-result without
    touching the queue (so disabling doesn't accidentally purge unsent
    events; use ``purge_queue`` for that).
    """
    if not is_enabled(state_dir):
        return PostResult(sent=0, accepted=0, rejected=0, error=None)

    dir_path = get_state_dir(state_dir)
    queue_path = _queue_path(dir_path)
    if not queue_path.exists():
        return PostResult(sent=0, accepted=0, rejected=0, error=None)

    try:
        lines = queue_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return PostResult(sent=0, accepted=None, rejected=None, error=f"read_failed:{exc}")

    events: list[dict[str, Any]] = []
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            events.append(json.loads(raw))
        except json.JSONDecodeError:
            continue

    if not events:
        queue_path.unlink(missing_ok=True)
        return PostResult(sent=0, accepted=0, rejected=0, error=None)

    if poster is None:
        poster = get_default_poster(state_dir)

    result = poster.post(events)

    if result.error is None:
        queue_path.unlink(missing_ok=True)

    return result


# ---- atexit hook ------------------------------------------------------------


def _atexit_flush_with_timeout(state_dir: str | Path | None) -> None:
    """Best-effort flush on interpreter exit. Capped at 2s total.

    Runs the flush on a daemon thread so we never block process shutdown
    past the timeout. If the flush is slow (network), we just leave events
    queued for next run.
    """
    if not is_enabled(state_dir):
        return

    def _run() -> None:
        try:
            flush(state_dir=state_dir)
        except Exception:  # noqa: BLE001 — atexit must never raise
            pass

    thread = threading.Thread(target=_run, daemon=True, name="repoctx-reporting-flush")
    thread.start()
    thread.join(timeout=ATEXIT_FLUSH_TIMEOUT_SECONDS)


def _ensure_atexit_flush(state_dir: str | Path | None) -> None:
    global _ATEXIT_REGISTERED
    if _ATEXIT_REGISTERED:
        return
    atexit.register(_atexit_flush_with_timeout, state_dir)
    _ATEXIT_REGISTERED = True


# ---- Canary disclosure ------------------------------------------------------

CANARY_NOTICE = (
    "[repoctx canary] Anonymous usage reporting is ON by default on canary "
    "builds — counts/timings/error-classes only, never paths or code. "
    "Disable: `repoctx reporting off` or REPOCTX_REPORTING=off. "
    "Inspect: `repoctx reporting show`."
)


def maybe_show_canary_notice(
    *,
    state_dir: str | Path | None = None,
    stream: Any = None,
) -> bool:
    """Print the canary disclosure once per install. No-op on stable.

    Returns True if the notice was printed this call. Writes to stderr by
    default — never stdout — so it doesn't corrupt MCP stdio framing.
    """
    if CHANNEL != "canary":
        return False

    state = load_state(state_dir)
    if state.canary_notice_shown:
        return False

    if stream is None:
        stream = sys.stderr
    try:
        print(CANARY_NOTICE, file=stream)
    except OSError:
        return False

    state.canary_notice_shown = True
    save_state(state, state_dir=state_dir)
    return True


# ---- Convenience for callers -----------------------------------------------


def reset_for_test() -> None:
    """Reset module-level caches. Tests call this between cases."""
    global _ATEXIT_REGISTERED
    _reset_install_id_cache()
    _ATEXIT_REGISTERED = False
