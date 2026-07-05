"""Zero-setup background provisioning of semantic retrieval.

Cloud sessions (Claude Code on the web, Codex cloud) run in ephemeral
containers. The MCP *connection* bootstraps itself (see
``harness.claude_code.portable_mcp_server_config``), but semantic retrieval
additionally needs three heavy pieces: the ``[embeddings]`` extra, the
embedding model download, and a built index. Historically those required the
user to configure an environment setup script per repo; this module makes
them automatic — a daemon thread installs the extra into the *running*
interpreter's environment, flips ``embeddings.HAS_EMBEDDINGS`` via
``refresh_embeddings_availability()``, and builds the index. The server keeps
serving lexical results (loudly) meanwhile and upgrades to semantic
mid-session on the next tool call — nothing restarts.

Gating (deliberately conservative):

- Default ON only when ``CLAUDE_CODE_REMOTE=true`` — a disposable container
  the user isn't billed disk/bandwidth on the way they are on a laptop.
- ``REPOCTX_AUTO_EMBEDDINGS=1`` forces it on anywhere (e.g. Codex cloud,
  which sets no marker env var); ``=0`` is the kill switch everywhere.
- A recorded ``"declined"`` index consent always wins — automation never
  overrides an explicit no. In remote containers, consent is otherwise
  recorded as ``"granted"`` before the build so the one-shot consent prompt
  (designed for *local* machines, where the user is present to answer)
  doesn't fire mid-session.

State is journaled to ``<index-state-root>/state/autoprovision.json`` so
concurrent server processes don't stampede the download and so retrieval
status messages (``embeddings._autoprovision_note``) can tell the agent
provisioning is underway rather than prescribing a manual fix.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ENV_AUTO_EMBEDDINGS = "REPOCTX_AUTO_EMBEDDINGS"
ENV_REMOTE_MARKER = "CLAUDE_CODE_REMOTE"

INSTALL_SPEC = "repoctx-mcp[embeddings]"
# CPU wheels keep the torch download ~5x smaller than the default CUDA build;
# cloud sessions have no GPU. Listed FIRST (--index-url) with PyPI as the
# extra so both pip and uv resolve torch from here and everything else from
# PyPI — matching scripts/cloud-setup.sh.
TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"
PYPI_INDEX = "https://pypi.org/simple"

_INSTALL_TIMEOUT_SECONDS = 1200  # cold CPU-torch install through a proxy is minutes, not seconds
# A "started"/"installing" stamp older than this is a crashed/killed run —
# ignore it and provision again rather than staying lexical forever.
_STALE_STATE_SECONDS = 45 * 60

# In-process single-flight: one provisioning attempt per server process.
_lock = threading.Lock()
_started_for: set[str] = set()


# ---- gating -----------------------------------------------------------------


def auto_provision_enabled() -> bool:
    """Whether background provisioning may run at all (env-level gate)."""
    raw = os.environ.get(ENV_AUTO_EMBEDDINGS, "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return os.environ.get(ENV_REMOTE_MARKER, "").strip().lower() == "true"


def _is_remote() -> bool:
    return os.environ.get(ENV_REMOTE_MARKER, "").strip().lower() == "true"


# ---- state file -------------------------------------------------------------


def _state_path(repo_root: str | Path) -> Path:
    from repoctx.index_location import index_state_root

    return index_state_root(repo_root) / "state" / "autoprovision.json"


def provisioning_state(repo_root: str | Path) -> dict[str, Any] | None:
    """The journaled provisioning state for ``repo_root``, or None."""
    try:
        raw = _state_path(repo_root).read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_state(repo_root: str | Path, status: str, detail: str = "", error: str = "") -> None:
    """Best-effort journal write; provisioning never fails on telemetry."""
    try:
        path = _state_path(repo_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        prior = provisioning_state(repo_root) or {}
        payload = {
            "status": status,
            "detail": detail,
            "error": error,
            "pid": os.getpid(),
            "started_at": prior.get("started_at") or time.time(),
            "updated_at": time.time(),
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError:
        logger.debug("could not write autoprovision state", exc_info=True)


def _state_blocks_start(repo_root: str | Path) -> bool:
    """True when another live run's stamp says provisioning is in progress.

    Terminal states (``ready``/``failed``/``declined``) don't block — ``ready``
    is re-checked against reality by the caller, and a failed run may be
    retried by a later session. An in-progress stamp older than the staleness
    window is treated as a crashed run and doesn't block either.
    """
    state = provisioning_state(repo_root)
    if not state:
        return False
    if state.get("status") not in {"installing", "building_index", "started"}:
        return False
    updated = state.get("updated_at")
    if not isinstance(updated, (int, float)):
        return False
    return (time.time() - updated) < _STALE_STATE_SECONDS


def provisioning_note(repo_root: str | Path) -> str:
    """Suffix for degraded-retrieval messages describing provisioning progress."""
    state = provisioning_state(repo_root)
    if not state:
        return ""
    status = state.get("status")
    detail = state.get("detail") or ""
    if status in {"started", "installing", "building_index"}:
        step = detail or status
        return (
            f" Semantic retrieval is being provisioned automatically in the "
            f"background ({step}); no action needed — results upgrade to "
            f"semantic once it completes."
        )
    if status == "failed":
        err = state.get("error") or "unknown error"
        return (
            f" (Automatic provisioning failed: {err}. Retrieval stays "
            f"lexical-only until the fix above is applied.)"
        )
    return ""


# ---- provisioning steps -----------------------------------------------------


def _deps_importable() -> bool:
    from repoctx.embeddings import refresh_embeddings_availability

    return refresh_embeddings_availability()


def _install_command() -> list[str]:
    """Build the installer argv for the *current* interpreter's environment.

    Prefers ``uv pip --python <this interpreter>`` when uv is available — it
    works on uv-managed ephemeral envs (which ship without pip), is fast, and
    sidesteps PEP 668. Falls back to ``python -m pip``. Both pin the torch CPU
    index first so Linux containers don't pull the multi-GB CUDA build.
    """
    uv = shutil.which("uv")
    if uv:
        return [
            uv, "pip", "install",
            "--python", sys.executable,
            "--index-url", TORCH_CPU_INDEX,
            "--extra-index-url", PYPI_INDEX,
            "--index-strategy", "unsafe-best-match",
            INSTALL_SPEC,
        ]
    return [
        sys.executable, "-m", "pip", "install", "--quiet",
        "--index-url", TORCH_CPU_INDEX,
        "--extra-index-url", PYPI_INDEX,
        INSTALL_SPEC,
    ]


def _install_embedding_deps() -> tuple[bool, str]:
    """Install the [embeddings] extra into the running interpreter's env."""
    cmd = _install_command()
    logger.info("autoprovision: installing embedding deps: %s", " ".join(cmd))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_INSTALL_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        return False, "install exited {}: {}".format(proc.returncode, " | ".join(tail))
    return True, ""


def _provision(repo_root: Path, telemetry_dir: str | Path | None = None) -> str:
    """Run the full provisioning sequence. Returns the terminal status string.

    Runs on a daemon thread in the MCP server (or synchronously from the
    ``repoctx autoprovision`` CLI). Every step is best-effort: a failure
    journals ``failed`` and leaves retrieval lexical-only with a loud warning,
    never breaks serving.
    """
    from repoctx.index_consent import read_consent, set_consent

    try:
        if read_consent(repo_root) == "declined":
            _write_state(repo_root, "declined", "user previously declined the index")
            return "declined"

        if not _deps_importable():
            _write_state(repo_root, "installing", "installing repoctx-mcp[embeddings] (CPU torch)")
            ok, err = _install_embedding_deps()
            if not ok:
                _write_state(repo_root, "failed", "dependency install", err)
                return "failed"
            if not _deps_importable():
                _write_state(
                    repo_root, "failed", "dependency install",
                    "packages installed but sentence-transformers still not importable",
                )
                return "failed"

        # Record consent before the build so the one-shot prompt never fires
        # for a repo we're provisioning automatically. Only ever upgrades
        # "unanswered" → "granted"; an explicit answer is never rewritten.
        if read_consent(repo_root) is None:
            try:
                set_consent(repo_root, "granted")
                _record_auto_consent(repo_root, telemetry_dir)
            except Exception:  # noqa: BLE001
                logger.debug("autoprovision: could not record consent", exc_info=True)

        # Containers are CPU-only; skip accelerator probing unless the user
        # explicitly picked a device.
        if _is_remote():
            os.environ.setdefault("REPOCTX_EMBEDDING_DEVICE", "cpu")

        _write_state(
            repo_root, "building_index",
            "downloading the embedding model on first run, then embedding origin/main",
        )
        from repoctx.embeddings import refresh_base_index

        result = refresh_base_index(repo_root, build_if_missing=True)
        status = str(result.get("status", ""))
        if status in {"built", "refreshed", "current"}:
            _write_state(repo_root, "ready", f"index {status}")
            logger.info("autoprovision: semantic retrieval ready (index %s)", status)
            return "ready"
        _write_state(repo_root, "failed", "index build", f"refresh_base_index → {status or 'unknown'}")
        return "failed"
    except Exception as exc:  # noqa: BLE001 — a background thread must not die loudly
        logger.warning("autoprovision failed", exc_info=True)
        _write_state(repo_root, "failed", "unexpected error", f"{type(exc).__name__}: {exc}")
        return "failed"


def _record_auto_consent(repo_root: Path, telemetry_dir: str | Path | None) -> None:
    from uuid import uuid4

    from repoctx.telemetry import record_index_consent_event

    record_index_consent_event(
        telemetry_dir=telemetry_dir,
        session_id=uuid4().hex,
        surface="autoprovision",
        action="granted",
        repo_root=repo_root,
        previous_action=None,
    )


# ---- entry point ------------------------------------------------------------


def maybe_start_auto_provision(
    repo_root: str | Path,
    telemetry_dir: str | Path | None = None,
) -> bool:
    """Start background provisioning for ``repo_root`` if it's needed.

    Cheap after the first call (env gate + an in-process set lookup), so it's
    safe to invoke from hot paths — the MCP server calls it from
    ``_ensure_embeddings`` (covering the startup warm thread) and ``_run_op``.
    Returns True iff a provisioning thread was started by *this* call.
    """
    if not auto_provision_enabled():
        return False
    root = Path(repo_root).resolve()
    key = str(root)
    with _lock:
        if key in _started_for:
            return False
        _started_for.add(key)

    try:
        from repoctx.index_consent import is_index_present, read_consent

        # Nothing to do when everything is already live, or explicitly refused.
        if _deps_importable() and is_index_present(root):
            return False
        if read_consent(root) == "declined":
            return False
        if _state_blocks_start(root):
            logger.info("autoprovision: another live run is provisioning %s; not starting", root)
            return False
    except Exception:  # noqa: BLE001 — gating must never break the caller
        logger.debug("autoprovision gate check failed; not starting", exc_info=True)
        return False

    _write_state(root, "started", "provisioning queued")
    threading.Thread(
        target=_provision,
        args=(root, telemetry_dir),
        name="repoctx-autoprovision",
        daemon=True,
    ).start()
    logger.info("autoprovision: background provisioning started for %s", root)
    return True


__all__ = [
    "ENV_AUTO_EMBEDDINGS",
    "ENV_REMOTE_MARKER",
    "auto_provision_enabled",
    "maybe_start_auto_provision",
    "provisioning_note",
    "provisioning_state",
]
