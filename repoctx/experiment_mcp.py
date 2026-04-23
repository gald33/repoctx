"""Time-limited MCP suppression for experiment control lanes.

Cursor keeps RepoCtx registered globally; during a control lane we instead
short-circuit MCP tools while ``suppressed`` is active.  Suppression auto-clears
after an idle TTL so a crashed CLI session does not leave tools disabled forever.

User settings live in ``~/.repoctx/config.json`` (override with
``REPOCTX_CONFIG_PATH``). State is stored beside telemetry in
``experiment-mcp-suppress.json``.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repoctx.telemetry import get_telemetry_dir

CONFIG_ENV = "REPOCTX_CONFIG_PATH"
STATE_FILENAME = "experiment-mcp-suppress.json"


@dataclass(frozen=True, slots=True)
class ExperimentMcpUserConfig:
    """Fields read from ~/.repoctx/config.json."""

    suppress_enabled: bool
    """When False, never arm suppression (legacy warning-only behaviour)."""

    idle_ttl_seconds: float
    """If no ``repoctx`` CLI runs extend the window before this elapses, clear suppression."""

    extend_seconds: float
    """Each ``repoctx`` CLI invocation while suppression is active pushes ``until`` forward by this amount."""


def default_user_config() -> ExperimentMcpUserConfig:
    return ExperimentMcpUserConfig(
        suppress_enabled=True,
        idle_ttl_seconds=90.0,
        extend_seconds=600.0,
    )


def user_config_path() -> Path:
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".repoctx" / "config.json"


def load_user_config() -> ExperimentMcpUserConfig:
    path = user_config_path()
    defaults = default_user_config()
    if not path.exists():
        return defaults
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return defaults
    if not isinstance(raw, dict):
        return defaults

    def _bool(key: str, default: bool) -> bool:
        v = raw.get(key)
        if isinstance(v, bool):
            return v
        return default

    def _float(key: str, default: float) -> float:
        v = raw.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
        return default

    idle_ttl = _float("experiment_mcp_idle_ttl_seconds", defaults.idle_ttl_seconds)
    extend = _float("experiment_mcp_extend_seconds", defaults.extend_seconds)
    return ExperimentMcpUserConfig(
        suppress_enabled=_bool("experiment_mcp_suppress", defaults.suppress_enabled),
        idle_ttl_seconds=max(5.0, idle_ttl),
        extend_seconds=max(5.0, extend),
    )


def _suppress_state_path(telemetry_dir: str | Path | None) -> Path:
    return get_telemetry_dir(telemetry_dir) / STATE_FILENAME


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _load_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def clear_mcp_suppression(*, telemetry_dir: str | Path | None = None) -> None:
    path = _suppress_state_path(telemetry_dir)
    path.unlink(missing_ok=True)


def arm_control_lane_mcp_suppression(
    *,
    telemetry_dir: str | Path | None = None,
    user: ExperimentMcpUserConfig | None = None,
) -> bool:
    """Arm suppression for the control lane. Returns False if disabled in user config."""
    cfg = user or load_user_config()
    if not cfg.suppress_enabled:
        return False
    now = time.time()
    path = _suppress_state_path(telemetry_dir)
    prev = _load_state(path)
    if prev and prev.get("suppressed"):
        try:
            prev_until = float(prev["until_unix"])
        except (TypeError, ValueError):
            prev_until = now
        started = prev.get("armed_at_unix")
        try:
            armed_at = float(started) if started is not None else now
        except (TypeError, ValueError):
            armed_at = now
        until = max(prev_until, now + cfg.idle_ttl_seconds)
        payload = {"suppressed": True, "until_unix": until, "armed_at_unix": armed_at}
    else:
        payload = {
            "suppressed": True,
            "until_unix": now + cfg.idle_ttl_seconds,
            "armed_at_unix": now,
        }
    _atomic_write_json(path, payload)
    return True


def refresh_after_cli_invocation(*, telemetry_dir: str | Path | None = None) -> None:
    """Called on every ``repoctx`` CLI entry: expire idle windows; prolong active suppression."""
    cfg = load_user_config()
    path = _suppress_state_path(telemetry_dir)
    state = _load_state(path)
    if not state:
        return
    if not state.get("suppressed"):
        path.unlink(missing_ok=True)
        return
    try:
        until = float(state["until_unix"])
    except (KeyError, TypeError, ValueError):
        path.unlink(missing_ok=True)
        return

    now = time.time()
    if now >= until:
        path.unlink(missing_ok=True)
        return

    if not cfg.suppress_enabled:
        path.unlink(missing_ok=True)
        return

    new_until = max(until, now + cfg.extend_seconds)
    state["until_unix"] = new_until
    _atomic_write_json(path, state)


def mcp_suppression_should_short_circuit(*, telemetry_dir: str | Path | None = None) -> bool:
    """True if MCP tools must return an empty / unavailable payload."""
    cfg = load_user_config()
    if not cfg.suppress_enabled:
        return False
    path = _suppress_state_path(telemetry_dir)
    state = _load_state(path)
    if not state or not state.get("suppressed"):
        return False
    try:
        until = float(state["until_unix"])
    except (KeyError, TypeError, ValueError):
        path.unlink(missing_ok=True)
        return False

    if time.time() >= until:
        path.unlink(missing_ok=True)
        return False
    return True


def control_lane_suppression_notice(
    *, armed: bool, telemetry_dir: str | Path | None = None
) -> str | None:
    """User-facing lines after arming or when resuming the control lane."""
    cfg = load_user_config()
    if not cfg.suppress_enabled or not armed:
        return None
    if not mcp_suppression_should_short_circuit(telemetry_dir=telemetry_dir):
        return None
    path = _suppress_state_path(telemetry_dir)
    state = _load_state(path) or {}
    try:
        until = float(state["until_unix"])
    except (TypeError, ValueError):
        return None
    remaining = int(max(0.0, until - time.time()))
    return (
        "RepoCtx MCP tools will return an empty stub during this control lane "
        f"(reverts automatically after ~{remaining}s idle, or when the treatment lane starts). "
        f"Run any `repoctx` command to extend by {int(cfg.extend_seconds)}s. "
        "Disable via experiment_mcp_suppress=false in ~/.repoctx/config.json."
    )
