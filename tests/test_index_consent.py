"""Tests for the one-shot index-consent prompt.

Covers both the pure module (`repoctx.index_consent`) and its wiring into the
MCP tool surface (`bundle`, `scope`, `semantic_search`, `get_task_context`,
plus the new `index` tool).

Per `feedback-repoctx-conventions` we never load the real embedding model: the
tests that need `HAS_EMBEDDINGS=True` patch it explicitly. Tests that simulate
a built index either patch `HAS_EMBEDDINGS=True` + create a non-empty
``.repoctx/embeddings/`` dir (sufficient for `is_index_present`) or rely on
the module's missing-index code path.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from repoctx import index_consent
from repoctx.index_consent import (
    CONSENT_KEY,
    PROMPTED_AT_KEY,
    attach_consent_metadata,
    is_index_present,
    maybe_consent_prompt,
    read_consent,
    set_consent,
    was_prompt_shown,
)
from repoctx.mcp_server import create_server


@pytest.fixture()
def tmp_repo(tmp_path: Path) -> Path:
    """A tmp dir that repoctx's repo-root resolver will accept (has .git)."""
    (tmp_path / ".git").mkdir()
    return tmp_path


def _get_tool(server, name: str):
    for tool in server._tool_manager.list_tools():
        if tool.name == name:
            return tool
    raise AssertionError(f"tool {name!r} not registered")


def _config(repo_root: Path) -> dict:
    cfg = repo_root / ".repoctx" / "config.json"
    if not cfg.exists():
        return {}
    return json.loads(cfg.read_text(encoding="utf-8"))


def _seed_built_index(repo_root: Path) -> None:
    """Create a non-empty `.repoctx/embeddings/` so is_index_present returns True.

    We don't need a real VectorIndex on disk — `is_index_present` only checks
    for a non-empty directory. Tests that need an actual loadable index would
    use the embedding spy pattern from `tests/test_embeddings.py`.
    """
    emb = repo_root / ".repoctx" / "embeddings"
    emb.mkdir(parents=True, exist_ok=True)
    (emb / "marker").write_text("present", encoding="utf-8")


# --- pure module ------------------------------------------------------------


def test_maybe_consent_prompt_returns_prompt_then_marks_shown(tmp_repo: Path) -> None:
    with patch("repoctx.index_consent.embeddings_available", return_value=True):
        prompt = maybe_consent_prompt(tmp_repo)

    assert prompt is not None
    assert prompt["type"] == "index_consent_required"
    assert prompt["ask_once"] is True
    # Both an accept and a decline action are advertised so the agent has a
    # clear path either way.
    assert prompt["actions"]["accept"]["tool"] == "index"
    assert prompt["actions"]["decline"]["tool"] == "index"
    assert prompt["actions"]["decline"]["args"] == {"decline": True}
    # The user-facing copy must mention the headline cost so consent is informed.
    assert "Qwen3-Embedding-0.6B" in prompt["user_message"]
    assert "600 MB" in prompt["user_message"]

    # Side-effect: prompt-shown marker is now persisted.
    assert was_prompt_shown(tmp_repo)
    assert PROMPTED_AT_KEY in _config(tmp_repo)


def test_maybe_consent_prompt_returns_none_after_first_call(tmp_repo: Path) -> None:
    """Once shown, the prompt never re-appears (the 'ask once' guarantee)."""
    with patch("repoctx.index_consent.embeddings_available", return_value=True):
        assert maybe_consent_prompt(tmp_repo) is not None
        assert maybe_consent_prompt(tmp_repo) is None


def test_maybe_consent_prompt_skipped_when_index_present(tmp_repo: Path) -> None:
    _seed_built_index(tmp_repo)
    with patch("repoctx.index_consent.embeddings_available", return_value=True):
        assert maybe_consent_prompt(tmp_repo) is None
    # And no prompt-shown marker was written.
    assert not was_prompt_shown(tmp_repo)


def test_maybe_consent_prompt_skipped_when_extras_missing(tmp_repo: Path) -> None:
    """Don't prompt if extras aren't installed — user can't act on it via the agent."""
    with patch("repoctx.index_consent.embeddings_available", return_value=False):
        assert maybe_consent_prompt(tmp_repo) is None
    assert not was_prompt_shown(tmp_repo)


def test_maybe_consent_prompt_skipped_after_explicit_answer(tmp_repo: Path) -> None:
    set_consent(tmp_repo, "granted")
    with patch("repoctx.index_consent.embeddings_available", return_value=True):
        assert maybe_consent_prompt(tmp_repo) is None
    set_consent(tmp_repo, "declined")
    with patch("repoctx.index_consent.embeddings_available", return_value=True):
        assert maybe_consent_prompt(tmp_repo) is None


def test_set_consent_persists_and_reads_back(tmp_repo: Path) -> None:
    assert read_consent(tmp_repo) is None
    set_consent(tmp_repo, "granted")
    assert read_consent(tmp_repo) == "granted"
    assert _config(tmp_repo)[CONSENT_KEY] == "granted"
    # Setting consent also marks "prompted" so we never re-ask after an answer.
    assert was_prompt_shown(tmp_repo)


def test_set_consent_rejects_invalid_value(tmp_repo: Path) -> None:
    with pytest.raises(ValueError):
        set_consent(tmp_repo, "maybe")  # type: ignore[arg-type]


def test_is_index_present_treats_empty_dir_as_absent(tmp_repo: Path) -> None:
    (tmp_repo / ".repoctx" / "embeddings").mkdir(parents=True)
    # Empty dir from an interrupted build shouldn't count as a built index.
    assert is_index_present(tmp_repo) is False
    (tmp_repo / ".repoctx" / "embeddings" / "x").write_text("y", encoding="utf-8")
    assert is_index_present(tmp_repo) is True


def test_attach_consent_metadata_wraps_dict_payload(tmp_repo: Path) -> None:
    with patch("repoctx.index_consent.embeddings_available", return_value=True):
        out = attach_consent_metadata({"hello": "world"}, tmp_repo)
    assert out["hello"] == "world"
    assert "index_consent_prompt" in out
    # Second call (steady state): no prompt key.
    with patch("repoctx.index_consent.embeddings_available", return_value=True):
        out2 = attach_consent_metadata({"hello": "world"}, tmp_repo)
    assert out2 == {"hello": "world"}


def test_attach_consent_metadata_wraps_list_payload(tmp_repo: Path) -> None:
    with patch("repoctx.index_consent.embeddings_available", return_value=True):
        out = attach_consent_metadata([{"path": "a"}], tmp_repo)
    assert isinstance(out, dict)
    assert out["results"] == [{"path": "a"}]
    assert out["index_consent_prompt"]["type"] == "index_consent_required"


def test_attach_consent_metadata_declined_dict_gets_quiet_hint(tmp_repo: Path) -> None:
    set_consent(tmp_repo, "declined")
    with patch("repoctx.index_consent.embeddings_available", return_value=True):
        out = attach_consent_metadata({"hello": "world"}, tmp_repo)
    assert isinstance(out, dict)
    assert out["hello"] == "world"
    assert out[CONSENT_KEY] == "declined"
    # No prompt is re-surfaced after an explicit decline.
    assert "index_consent_prompt" not in out


def test_attach_consent_metadata_declined_list_stays_list(tmp_repo: Path) -> None:
    """Declined list payloads keep their historical shape (no wrap, no metadata).

    Wrapping a list into a dict in steady-state would break the documented
    contract of `semantic_search` for every subsequent call.
    """
    set_consent(tmp_repo, "declined")
    with patch("repoctx.index_consent.embeddings_available", return_value=True):
        out = attach_consent_metadata([{"path": "a"}], tmp_repo)
    assert out == [{"path": "a"}]


def test_attach_consent_metadata_handles_internal_failure(tmp_repo: Path) -> None:
    """A bug in consent-checking must never break the underlying tool call."""
    with patch.object(index_consent, "maybe_consent_prompt", side_effect=RuntimeError("boom")):
        out = attach_consent_metadata({"hello": "world"}, tmp_repo)
    assert out == {"hello": "world"}


# --- MCP wiring -------------------------------------------------------------


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_get_task_context_includes_consent_prompt_on_first_call(tmp_repo: Path) -> None:
    _write_file(tmp_repo / "AGENTS.md", "# Repo guidance\n")
    with patch("repoctx.index_consent.embeddings_available", return_value=True):
        server = create_server(repo_root=tmp_repo)
        tool = _get_tool(server, "get_task_context")
        first = tool.fn(task="retry")
        second = tool.fn(task="retry")
    assert "index_consent_prompt" in first
    assert first["index_consent_prompt"]["actions"]["accept"]["tool"] == "index"
    # Second call: marker is set, prompt is suppressed.
    assert "index_consent_prompt" not in second


def test_bundle_includes_consent_prompt_on_first_call(tmp_repo: Path) -> None:
    _write_file(tmp_repo / "AGENTS.md", "# Repo guidance\n")
    _write_file(tmp_repo / "src" / "retry.py", "def retry():\n    return True\n")
    with patch("repoctx.index_consent.embeddings_available", return_value=True):
        server = create_server(repo_root=tmp_repo)
        tool = _get_tool(server, "bundle")
        first = tool.fn(task="retry")
        second = tool.fn(task="retry")
    assert "index_consent_prompt" in first
    assert "index_consent_prompt" not in second


def test_semantic_search_wraps_list_on_first_call(tmp_repo: Path) -> None:
    """semantic_search returns a dict (wrapping the list) only on the cold-start
    call; once the prompt has been shown, the response goes back to a bare list.
    """
    _write_file(tmp_repo / "src" / "retry.py", "def retry():\n    return True\n")
    with patch("repoctx.index_consent.embeddings_available", return_value=True):
        server = create_server(repo_root=tmp_repo)
        tool = _get_tool(server, "semantic_search")
        first = tool.fn(query="retry")
        second = tool.fn(query="retry")
    assert isinstance(first, dict)
    assert first["results"] == []  # no index → no hits
    assert "index_consent_prompt" in first
    assert isinstance(second, list)
    assert second == []


def test_index_tool_decline_records_consent(tmp_repo: Path) -> None:
    server = create_server(repo_root=tmp_repo)
    tool = _get_tool(server, "index")
    result = tool.fn(decline=True)
    assert result["status"] == "declined"
    assert read_consent(tmp_repo) == "declined"
    assert was_prompt_shown(tmp_repo)


def test_index_tool_decline_suppresses_future_prompts(tmp_repo: Path) -> None:
    _write_file(tmp_repo / "AGENTS.md", "# Repo guidance\n")
    with patch("repoctx.index_consent.embeddings_available", return_value=True):
        server = create_server(repo_root=tmp_repo)
        index_tool = _get_tool(server, "index")
        index_tool.fn(decline=True)
        bundle_tool = _get_tool(server, "bundle")
        result = bundle_tool.fn(task="retry")
    assert "index_consent_prompt" not in result
    # And the declined hint is present so the agent knows why retrieval is lexical.
    assert result.get(CONSENT_KEY) == "declined"


def test_index_tool_skips_build_when_extras_missing(tmp_repo: Path) -> None:
    server = create_server(repo_root=tmp_repo)
    tool = _get_tool(server, "index")
    with patch("repoctx.embeddings.HAS_EMBEDDINGS", False):
        result = tool.fn()
    assert result["status"] == "error"
    assert "embeddings" in result["errors"]["embedding_index"].lower()
    # We did NOT auto-grant — granted only on successful build.
    assert read_consent(tmp_repo) is None


def test_index_tool_records_granted_on_successful_build(tmp_repo: Path, monkeypatch) -> None:
    """A successful build flips consent to 'granted'.

    We stub `_maybe_build_index` so we don't load the embedding model in tests
    (per `feedback-repoctx-conventions`: the model is a real heavyweight
    download). The stub returns the same shape the real function returns on
    success.
    """
    server = create_server(repo_root=tmp_repo)
    tool = _get_tool(server, "index")

    def fake_build(repo_root, build_index, errors):
        return {"status": "built", "files": 0, "index_dir": str(tmp_repo / ".repoctx" / "embeddings")}

    monkeypatch.setattr("repoctx.harness._maybe_build_index", fake_build)
    result = tool.fn()
    assert result["status"] == "built"
    assert read_consent(tmp_repo) == "granted"
