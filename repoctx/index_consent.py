"""One-shot user consent for building the embedding index from MCP.

The embedding index download (~600 MB for the Qwen3-Embedding-0.6B model) plus
the full-repo scan is the single largest "surprise cost" repoctx can incur.
CLI users opt in implicitly by running ``repoctx index``, but an MCP-driven
agent has no comparable signal — historically the retrieval tools just silently
degraded to lexical-only when the index was missing.

This module gives the MCP surface a one-shot consent hand-off:

* The first time a retrieval tool (bundle, semantic_search, scope, etc.) runs
  on a repo whose index doesn't exist, the response includes an
  ``index_consent_prompt`` payload telling the agent to ASK the user, verbatim,
  whether to build the index. The prompt copy spells out the model download,
  scan time, and impact on retrieval quality so the user can decide informed.
* The prompt is shown ONCE per repo. A marker (``index_consent_prompted_at``)
  is written to ``<repo>/.repoctx/config.json`` the moment the prompt is
  attached, so subsequent tool calls don't re-pester the user.
* The agent records the user's answer by calling the ``index`` MCP tool —
  either accepts (which both builds the index and records ``"granted"``) or
  declines (``index({decline: True})`` records ``"declined"`` without building).
* Declined repos surface a quiet ``index_consent: "declined"`` field on
  retrieval responses so the agent knows why retrieval is lexical-only; that
  signal is the only ongoing footprint after the one-shot prompt.

Storage shape in ``<repo>/.repoctx/config.json`` (top-level keys, alongside
``feedback_enabled`` and the ``learned`` block written by ``tune``):

    {
      "index_consent": "granted" | "declined",   # absent until user answers
      "index_consent_prompted_at": <epoch seconds>  # absent until first prompt
    }

Why both fields: ``index_consent`` records the user's *answer*; the timestamp
records that we *asked*. If the user ignored the prompt (never called
``index``), the answer stays absent but the timestamp prevents re-asking.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Literal

from repoctx.config import DEFAULT_EMBEDDING_CONFIG, EmbeddingConfig

logger = logging.getLogger(__name__)

CONSENT_KEY = "index_consent"
PROMPTED_AT_KEY = "index_consent_prompted_at"

ConsentValue = Literal["granted", "declined"]

# User-facing copy. Kept verbatim so the LLM can relay without summarizing —
# the cost numbers and behaviour-after-decline matter for informed consent.
_USER_MESSAGE = (
    "repoctx has no embedding index for this repo yet. Building one will:\n"
    "\n"
    "  - Download the Qwen3-Embedding-0.6B model (~600 MB, one-time per machine).\n"
    "  - Scan and embed every source file in the repo. Expect a few minutes on\n"
    "    a small repo; large monorepos can take significantly longer and use\n"
    "    noticeable CPU/RAM.\n"
    "  - Write the index to .repoctx/embeddings/ inside this repo.\n"
    "\n"
    "Without the index, repoctx falls back to lexical-only retrieval — still\n"
    "functional, but materially lower quality on tasks where the right files\n"
    "don't share keywords with the query.\n"
    "\n"
    "Would you like to build the embedding index now?"
)

_AGENT_INSTRUCTIONS = (
    "Relay `user_message` to the user verbatim and wait for their explicit "
    "answer. Do NOT assume consent and do NOT call the `index` tool until "
    "they have answered. If they accept, call the `index` MCP tool with no "
    "arguments. If they decline, call `index` with `decline=true` so the "
    "decision is recorded and this prompt won't be shown again. This prompt "
    "is shown ONLY ONCE per repo — after the user answers (or after this "
    "first response is returned), future retrieval tool calls will not "
    "include it."
)


def embeddings_dir(repo_root: str | Path, config: EmbeddingConfig = DEFAULT_EMBEDDING_CONFIG) -> Path:
    """Resolve ``<repo>/.repoctx/embeddings`` per the embedding config."""
    return Path(repo_root).resolve() / config.index_dir / "embeddings"


def is_index_present(repo_root: str | Path, config: EmbeddingConfig = DEFAULT_EMBEDDING_CONFIG) -> bool:
    """True if the persisted vector index directory exists and is non-empty.

    We check for directory contents (not just existence) so a leftover empty
    ``.repoctx/embeddings/`` from an interrupted build doesn't fool us.
    """
    d = embeddings_dir(repo_root, config)
    if not d.is_dir():
        return False
    try:
        return any(d.iterdir())
    except OSError:
        return False


def embeddings_available() -> bool:
    """True if the [embeddings] extras are importable.

    We never prompt for consent when extras are missing — the user can't act on
    the prompt from inside an agent session (they'd need to ``pip install
    'repoctx-mcp[embeddings]'`` first), so surfacing it would just be noise.
    """
    try:
        from repoctx.embeddings import HAS_EMBEDDINGS
    except ImportError:
        return False
    return bool(HAS_EMBEDDINGS)


def _config_path(repo_root: str | Path) -> Path:
    return Path(repo_root) / ".repoctx" / "config.json"


def _load_config(repo_root: str | Path) -> dict[str, Any]:
    path = _config_path(repo_root)
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Ignoring malformed repoctx config at %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _save_config(repo_root: str | Path, payload: dict[str, Any]) -> None:
    """Write *payload* to ``<repo>/.repoctx/config.json``, creating the dir.

    Mirrors ``tune.apply_tune``'s serialization style (sorted keys, indent=2,
    trailing newline) so a single repo doesn't churn between writers.
    """
    path = _config_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def read_consent(repo_root: str | Path) -> ConsentValue | None:
    """Return the recorded consent value, or None if the user hasn't answered."""
    data = _load_config(repo_root)
    value = data.get(CONSENT_KEY)
    if value in ("granted", "declined"):
        return value  # type: ignore[return-value]
    return None


def was_prompt_shown(repo_root: str | Path) -> bool:
    """True iff the consent prompt has been attached to a tool response before."""
    data = _load_config(repo_root)
    return PROMPTED_AT_KEY in data


def set_consent(repo_root: str | Path, value: ConsentValue) -> None:
    """Record the user's consent answer. Also sets the prompt-shown timestamp
    (if absent) so we never re-prompt after an explicit answer.
    """
    if value not in ("granted", "declined"):
        raise ValueError(f"invalid consent value: {value!r}")
    data = _load_config(repo_root)
    data[CONSENT_KEY] = value
    data.setdefault(PROMPTED_AT_KEY, int(time.time()))
    _save_config(repo_root, data)


def mark_prompt_shown(repo_root: str | Path) -> None:
    """Record that the consent prompt was surfaced to the agent.

    Idempotent: if the timestamp is already set, the existing value is kept so
    we preserve the *first* time we asked (useful for telemetry/debugging).
    """
    data = _load_config(repo_root)
    if PROMPTED_AT_KEY in data:
        return
    data[PROMPTED_AT_KEY] = int(time.time())
    _save_config(repo_root, data)


def _build_prompt_dict() -> dict[str, Any]:
    return {
        "type": "index_consent_required",
        "title": "Build embedding index?",
        "user_message": _USER_MESSAGE,
        "agent_instructions": _AGENT_INSTRUCTIONS,
        "actions": {
            "accept": {"tool": "index", "args": {}},
            "decline": {"tool": "index", "args": {"decline": True}},
        },
        "ask_once": True,
    }


def maybe_consent_prompt(repo_root: str | Path) -> dict[str, Any] | None:
    """Return a structured consent prompt iff we should ask the user, else None.

    Conditions for prompting:
      * The [embeddings] extras must be installed (otherwise the user can't act
        on the prompt from inside the agent).
      * The on-disk index must be missing.
      * The user must not have answered yet (no recorded consent).
      * We must not have shown the prompt before (no recorded timestamp).

    Side-effect: when we decide to prompt, ``mark_prompt_shown`` is called
    immediately so the *next* tool call won't re-attach the prompt. This is
    the "ask once" guarantee.
    """
    if not embeddings_available():
        return None
    try:
        if is_index_present(repo_root):
            return None
        if read_consent(repo_root) is not None:
            return None
        if was_prompt_shown(repo_root):
            return None
    except Exception:
        # Never let a consent-check failure break a tool call.
        logger.debug("index_consent check failed; skipping prompt", exc_info=True)
        return None

    try:
        mark_prompt_shown(repo_root)
    except Exception:
        # Even if we couldn't persist the marker, return the prompt this once.
        # Worst case: the next call also prompts (annoying, not broken).
        logger.warning("Failed to persist index_consent prompt marker", exc_info=True)
    return _build_prompt_dict()


def attach_consent_metadata(
    payload: dict[str, Any] | list[Any],
    repo_root: str | Path,
) -> dict[str, Any] | list[Any]:
    """Decorate a retrieval-tool response with consent metadata.

    Behaviour:
      * If we should prompt: attach the prompt dict under ``index_consent_prompt``.
        For dict payloads this is a top-level key; for list payloads (only
        ``semantic_search`` today) the response is wrapped as
        ``{"results": <list>, "index_consent_prompt": <prompt>}``.
      * If consent is recorded as ``"declined"``: attach a quiet
        ``index_consent: "declined"`` field so the agent knows retrieval is
        lexical-only by user choice (no prompt, no wrap of list payloads —
        list payloads stay lists in the steady-state declined path).
      * Otherwise: return *payload* unchanged.

    Never raises — wraps any internal error and returns the original payload.
    """
    try:
        prompt = maybe_consent_prompt(repo_root)
        declined = read_consent(repo_root) == "declined"
    except Exception:
        logger.debug("attach_consent_metadata failed; returning payload unchanged", exc_info=True)
        return payload

    if prompt is None and not declined:
        return payload

    if isinstance(payload, list):
        if prompt is not None:
            wrapped: dict[str, Any] = {"results": payload, "index_consent_prompt": prompt}
            return wrapped
        # Declined path: list payloads stay as lists (no metadata wrap) so the
        # historical contract isn't broken in steady state.
        return payload

    if not isinstance(payload, dict):
        # Unknown shape — don't touch it.
        return payload

    if prompt is not None:
        payload = {**payload, "index_consent_prompt": prompt}
    if declined:
        payload = {**payload, CONSENT_KEY: "declined"}
    return payload


__all__ = [
    "CONSENT_KEY",
    "PROMPTED_AT_KEY",
    "ConsentValue",
    "attach_consent_metadata",
    "embeddings_available",
    "embeddings_dir",
    "is_index_present",
    "mark_prompt_shown",
    "maybe_consent_prompt",
    "read_consent",
    "set_consent",
    "was_prompt_shown",
]
