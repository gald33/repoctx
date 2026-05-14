"""Tests for the per-repo config loader.

Covers the four precedence cases the loader is responsible for:
- absent config file → defaults
- partial overrides → only specified kinds change
- env var precedence over file values
- malformed/unknown input → logged and ignored, never raises
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repoctx.config import DEFAULT_CONFIG
from repoctx.config_loader import (
    CONFIG_DIR,
    CONFIG_FILENAME,
    is_feedback_enabled,
    load_repo_config,
)


def _write_config(repo: Path, payload: dict) -> Path:
    cfg_dir = repo / CONFIG_DIR
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / CONFIG_FILENAME
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_no_file_returns_defaults(tmp_path: Path):
    cfg = load_repo_config(tmp_path)
    assert cfg.qualify_threshold_for("code") == DEFAULT_CONFIG.qualify_threshold_for("code")
    assert cfg.qualify_threshold_for("doc") == DEFAULT_CONFIG.qualify_threshold_for("doc")
    assert cfg.lexical_tiebreak_for("config") == DEFAULT_CONFIG.lexical_tiebreak_for("config")


def test_partial_override_only_changes_named_kinds(tmp_path: Path):
    _write_config(tmp_path, {
        "embedding_qualify_thresholds": {"config": 0.5},
    })
    cfg = load_repo_config(tmp_path)
    assert cfg.qualify_threshold_for("config") == 0.5
    # Unmentioned kinds keep defaults
    assert cfg.qualify_threshold_for("code") == DEFAULT_CONFIG.qualify_threshold_for("code")
    assert cfg.qualify_threshold_for("doc") == DEFAULT_CONFIG.qualify_threshold_for("doc")
    # Other field (tiebreak) untouched
    assert cfg.lexical_tiebreak_for("code") == DEFAULT_CONFIG.lexical_tiebreak_for("code")


def test_both_fields_can_override(tmp_path: Path):
    _write_config(tmp_path, {
        "embedding_qualify_thresholds": {"code": 0.2, "doc": 0.4},
        "lexical_tiebreak_weights": {"code": 0.1},
    })
    cfg = load_repo_config(tmp_path)
    assert cfg.qualify_threshold_for("code") == 0.2
    assert cfg.qualify_threshold_for("doc") == 0.4
    assert cfg.lexical_tiebreak_for("code") == 0.1


def test_learned_section_overrides_root(tmp_path: Path):
    # learned (Phase 3 output) wins over user-set root values per documented
    # precedence; deleting the learned block restores hand-tuned values.
    _write_config(tmp_path, {
        "embedding_qualify_thresholds": {"code": 0.2},
        "learned": {
            "embedding_qualify_thresholds": {"code": 0.35},
        },
    })
    cfg = load_repo_config(tmp_path)
    assert cfg.qualify_threshold_for("code") == 0.35


def test_env_var_overrides_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _write_config(tmp_path, {
        "embedding_qualify_thresholds": {"code": 0.2},
    })
    monkeypatch.setenv("REPOCTX_QUALIFY_THRESHOLD_CODE", "0.45")
    cfg = load_repo_config(tmp_path)
    assert cfg.qualify_threshold_for("code") == 0.45


def test_env_var_for_tiebreak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("REPOCTX_LEXICAL_TIEBREAK_DOC", "0.2")
    cfg = load_repo_config(tmp_path)
    assert cfg.lexical_tiebreak_for("doc") == 0.2
    assert cfg.lexical_tiebreak_for("code") == DEFAULT_CONFIG.lexical_tiebreak_for("code")


def test_unknown_kind_in_file_is_ignored(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    _write_config(tmp_path, {
        "embedding_qualify_thresholds": {"banana": 0.9, "code": 0.25},
    })
    with caplog.at_level("WARNING"):
        cfg = load_repo_config(tmp_path)
    # Known kind still applies
    assert cfg.qualify_threshold_for("code") == 0.25
    # Unknown kind doesn't leak into the map (querying it returns _default)
    assert cfg.qualify_threshold_for("banana") == DEFAULT_CONFIG.qualify_threshold_for("_default")
    assert any("banana" in r.message for r in caplog.records)


def test_unknown_kind_in_env_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    monkeypatch.setenv("REPOCTX_QUALIFY_THRESHOLD_BANANA", "0.9")
    with caplog.at_level("WARNING"):
        cfg = load_repo_config(tmp_path)
    assert cfg.qualify_threshold_for("banana") == DEFAULT_CONFIG.qualify_threshold_for("_default")
    assert any("BANANA" in r.message or "banana" in r.message for r in caplog.records)


def test_malformed_file_falls_back_to_defaults(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    cfg_path = tmp_path / CONFIG_DIR / CONFIG_FILENAME
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("{not valid json", encoding="utf-8")
    with caplog.at_level("WARNING"):
        cfg = load_repo_config(tmp_path)
    assert cfg.qualify_threshold_for("code") == DEFAULT_CONFIG.qualify_threshold_for("code")


def test_non_object_root_is_ignored(tmp_path: Path):
    cfg_path = tmp_path / CONFIG_DIR / CONFIG_FILENAME
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    cfg = load_repo_config(tmp_path)
    assert cfg.qualify_threshold_for("code") == DEFAULT_CONFIG.qualify_threshold_for("code")


def test_non_numeric_value_is_ignored(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    _write_config(tmp_path, {
        "embedding_qualify_thresholds": {"code": "not a float"},
    })
    with caplog.at_level("WARNING"):
        cfg = load_repo_config(tmp_path)
    assert cfg.qualify_threshold_for("code") == DEFAULT_CONFIG.qualify_threshold_for("code")


def test_non_numeric_env_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    monkeypatch.setenv("REPOCTX_QUALIFY_THRESHOLD_CODE", "not a float")
    with caplog.at_level("WARNING"):
        cfg = load_repo_config(tmp_path)
    assert cfg.qualify_threshold_for("code") == DEFAULT_CONFIG.qualify_threshold_for("code")


def test_default_kind_used_for_unknown_record_kind(tmp_path: Path):
    _write_config(tmp_path, {
        "embedding_qualify_thresholds": {"_default": 0.42},
    })
    cfg = load_repo_config(tmp_path)
    # Any kind not in the map falls back to _default
    assert cfg.qualify_threshold_for("anything") == 0.42
    # Named kinds still resolve normally (they're in the map from defaults)
    assert cfg.qualify_threshold_for("code") == DEFAULT_CONFIG.qualify_threshold_for("code")


def test_feedback_enabled_defaults_true(tmp_path: Path):
    assert is_feedback_enabled(tmp_path) is True


def test_feedback_enabled_false(tmp_path: Path):
    _write_config(tmp_path, {"feedback_enabled": False})
    assert is_feedback_enabled(tmp_path) is False


def test_feedback_enabled_with_other_keys(tmp_path: Path):
    _write_config(tmp_path, {
        "feedback_enabled": True,
        "embedding_qualify_thresholds": {"code": 0.5},
    })
    assert is_feedback_enabled(tmp_path) is True
    cfg = load_repo_config(tmp_path)
    assert cfg.qualify_threshold_for("code") == 0.5
