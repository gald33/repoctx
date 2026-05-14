"""Per-repo config loader for retrieval scoring knobs.

Reads ``<repo>/.repoctx/config.json`` and merges it with ``DEFAULT_CONFIG``,
with env-var overrides on top. Phase 0 surface is intentionally narrow — only
the retrieval-scoring fields are loadable; structural fields like
``ignored_dirs``, ``max_files``, and the extension lists remain code-defined.

Precedence (lowest to highest):
  1. ``DEFAULT_CONFIG`` (per-kind defaults from ``config.py``)
  2. ``<repo>/.repoctx/config.json`` root keys (user overrides)
  3. ``<repo>/.repoctx/config.json`` ``learned`` section (written by future
     ``repoctx tune`` runs in Phase 3 — currently unused but reserved so the
     loader can already merge it without a schema bump)
  4. ``REPOCTX_QUALIFY_THRESHOLD_<KIND>`` / ``REPOCTX_LEXICAL_TIEBREAK_<KIND>``
     env vars (per-shell experimentation)

Note on precedence ordering: ``learned`` sits *above* root keys so a fitted
threshold can supersede a stale hand-set value, but env vars stay highest so
operators always have an escape hatch. If you want hand-tuned values to win
over learned ones, delete the ``learned`` block — that's the intended workflow.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

from repoctx.config import DEFAULT_CONFIG, RepoCtxConfig

logger = logging.getLogger(__name__)

CONFIG_FILENAME = "config.json"
CONFIG_DIR = ".repoctx"

_KNOWN_KINDS = ("code", "doc", "config", "test", "_default")
# Subkinds the built-in classifier emits. Override via per-repo config to
# extend; unknown subkinds are accepted with a debug log (so a forward-
# compatible config from a newer release doesn't crash an older binary).
_KNOWN_SUBKINDS_BY_KIND: dict[str, tuple[str, ...]] = {
    "code": ("handler", "model", "cli", "util", "scaffold", "generated", "other"),
    "doc": ("agent_contract", "architecture", "readme", "other"),
    "config": ("build", "ci", "lint", "other"),
    "test": (),
}
_QUALIFY_ENV_PREFIX = "REPOCTX_QUALIFY_THRESHOLD_"
_TIEBREAK_ENV_PREFIX = "REPOCTX_LEXICAL_TIEBREAK_"


def load_repo_config(
    repo_root: str | Path,
    base: RepoCtxConfig = DEFAULT_CONFIG,
) -> RepoCtxConfig:
    """Load and merge the per-repo retrieval config.

    Returns ``base`` unchanged if no config file is present and no env vars are
    set. Never raises on malformed config — logs a warning and falls back to
    the base value instead, so a broken file can't break retrieval.
    """
    qualify = dict(base.embedding_qualify_thresholds)
    tiebreak = dict(base.lexical_tiebreak_weights)

    file_payload = _read_config_file(Path(repo_root) / CONFIG_DIR / CONFIG_FILENAME)
    if file_payload:
        _apply_payload(file_payload, qualify, tiebreak, source="file")
        learned = file_payload.get("learned")
        if isinstance(learned, dict):
            _apply_payload(learned, qualify, tiebreak, source="learned")

    _apply_env(qualify, _QUALIFY_ENV_PREFIX, target_name="qualify_threshold")
    _apply_env(tiebreak, _TIEBREAK_ENV_PREFIX, target_name="lexical_tiebreak")

    return replace(
        base,
        embedding_qualify_thresholds=qualify,
        lexical_tiebreak_weights=tiebreak,
    )


def is_feedback_enabled(repo_root: str | Path) -> bool:
    """Return whether the per-repo feedback log is enabled. Defaults to True.

    Reserved for Phase 1's event-logging plumbing; lives in the loader so the
    opt-out lives next to the rest of per-repo config. Phase 0 doesn't read
    this yet — it's here so adding the Phase 1 instrumentation doesn't require
    re-plumbing config access.
    """
    payload = _read_config_file(Path(repo_root) / CONFIG_DIR / CONFIG_FILENAME)
    if not payload:
        return True
    value = payload.get("feedback_enabled", True)
    return bool(value)


def _read_config_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Ignoring malformed repoctx config at %s: %s", path, exc)
        return None
    if not isinstance(payload, dict):
        logger.warning("Ignoring repoctx config at %s: root must be a JSON object", path)
        return None
    return payload


def _apply_payload(
    payload: dict[str, Any],
    qualify: dict[str, float],
    tiebreak: dict[str, float],
    *,
    source: str,
) -> None:
    q = payload.get("embedding_qualify_thresholds")
    if q is not None:
        _merge_kind_map(q, qualify, field="embedding_qualify_thresholds", source=source)
    t = payload.get("lexical_tiebreak_weights")
    if t is not None:
        _merge_kind_map(t, tiebreak, field="lexical_tiebreak_weights", source=source)


def _merge_kind_map(
    incoming: Any,
    target: dict[str, float],
    *,
    field: str,
    source: str,
) -> None:
    if not isinstance(incoming, dict):
        logger.warning(
            "Ignoring %s.%s: expected an object of {kind: float}", source, field
        )
        return
    for raw_key, value in incoming.items():
        if not _is_valid_kind_key(raw_key):
            logger.warning(
                "Ignoring unknown kind %r in %s.%s (known kinds: %s; "
                "subkinds use 'kind/subkind' form)",
                raw_key, source, field, ", ".join(_KNOWN_KINDS),
            )
            continue
        try:
            target[raw_key] = float(value)
        except (TypeError, ValueError):
            logger.warning(
                "Ignoring non-numeric value %r for %s.%s.%s", value, source, field, raw_key
            )


def _is_valid_kind_key(key: Any) -> bool:
    """Accept either a known kind, or ``kind/subkind`` where kind is known.

    Subkinds aren't strictly validated against a fixed list — a newer
    classifier may emit a subkind an older binary doesn't recognize, and we
    don't want to reject forward-compatible configs. The lookup chain falls
    back to the parent kind anyway, so an unknown subkind is harmless.
    """
    if not isinstance(key, str) or not key:
        return False
    if "/" not in key:
        return key in _KNOWN_KINDS
    parent, _sub = key.split("/", 1)
    return parent in _KNOWN_KINDS and parent != "_default"


def _apply_env(target: dict[str, float], prefix: str, *, target_name: str) -> None:
    for key, raw in os.environ.items():
        if not key.startswith(prefix):
            continue
        kind = key[len(prefix):].lower()
        if kind not in _KNOWN_KINDS:
            logger.warning(
                "Ignoring env var %s: unknown kind %r (known: %s)",
                key, kind, ", ".join(_KNOWN_KINDS),
            )
            continue
        try:
            target[kind] = float(raw)
        except ValueError:
            logger.warning(
                "Ignoring env var %s=%r: not a valid float for %s",
                key, raw, target_name,
            )
