"""Tests for hierarchical tuning: subkind cells fall back to parent's fit."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from repoctx.feedback_log import append_event
from repoctx.tune import TuneConfig, apply_tune, tune


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _emit(
    repo: Path,
    bundle_id: str,
    path: str,
    full_kind: str,
    emb_score: float,
    *,
    label: dict,
    ts: datetime | None = None,
):
    ts = ts or datetime.now(timezone.utc)
    append_event(repo, {
        "event_type": "bundle_emitted",
        "bundle_id": bundle_id,
        "ranked_paths": [{
            "path": path, "kind": full_kind, "score": emb_score,
            "embedding_score": emb_score, "heuristic_score": 0.0,
        }],
        "event_time": _iso(ts),
    })
    evt = dict(label)
    evt["bundle_id"] = bundle_id
    evt["path"] = path
    evt["event_time"] = _iso(ts)
    append_event(repo, evt)


def test_subkind_fits_independently_when_data_supports_it(tmp_path: Path):
    cfg = TuneConfig(min_labels_per_kind=5, prior_sigma=0.15)
    # code/handler: 15 positives at emb=0.20 → push T down for handlers
    for i in range(15):
        _emit(
            tmp_path, f"h{i}", f"api/h{i}.py", "code/handler", 0.20,
            label={"event_type": "tool_use", "action": "Edit", "source": "hook"},
        )
    # code/model: 15 noise at emb=0.35 → push T up for models
    for i in range(15):
        _emit(
            tmp_path, f"m{i}", f"models/m{i}.py", "code/model", 0.35,
            label={"event_type": "self_report", "relevance": "noise", "source": "self_report"},
        )

    result = tune(tmp_path, config=cfg)
    fits_by_kind = {f.kind: f for f in result.fits}
    # Both subkinds should have moved away from their parent's prior.
    handler_t = fits_by_kind["code/handler"].fitted_threshold
    model_t = fits_by_kind["code/model"].fitted_threshold
    assert handler_t < model_t
    # And both should be recorded in learned_thresholds (since they moved).
    assert "code/handler" in result.learned_thresholds
    assert "code/model" in result.learned_thresholds


def test_thin_subkind_falls_back_to_parent_fit(tmp_path: Path):
    cfg = TuneConfig(min_labels_per_kind=10, prior_sigma=0.15)
    # 20 positives at the parent kind level (no subkind) → parent gets fit
    for i in range(20):
        _emit(
            tmp_path, f"p{i}", f"flat/p{i}.py", "code", 0.20,
            label={"event_type": "tool_use", "action": "Edit", "source": "hook"},
        )
    # Only 3 labels for code/handler → not enough to fit on its own
    for i in range(3):
        _emit(
            tmp_path, f"h{i}", f"api/h{i}.py", "code/handler", 0.50,
            label={"event_type": "tool_use", "action": "Edit", "source": "hook"},
        )
    result = tune(tmp_path, config=cfg)
    fits_by_kind = {f.kind: f for f in result.fits}

    parent_fit = fits_by_kind["code"]
    handler_fit = fits_by_kind["code/handler"]
    # Parent got fit (data-rich).
    assert parent_fit.confidence in ("weak", "strong")
    # Handler subkind was thin — fitted == prior, which is the parent's fitted T.
    assert handler_fit.confidence == "prior_only"
    assert handler_fit.prior_threshold == parent_fit.fitted_threshold
    assert handler_fit.fitted_threshold == parent_fit.fitted_threshold


def test_thin_subkind_omitted_from_learned(tmp_path: Path):
    # Subkinds with < min_labels stay in ``prior_only`` confidence and are
    # NOT written — the loader's fallback chain (subkind → parent → default)
    # picks up the parent's value at read time, so writing the redundant
    # entry would just clutter the config and block future re-fits.
    cfg = TuneConfig(min_labels_per_kind=10, prior_sigma=0.15)
    # Plenty of parent-level labels → parent gets fit.
    for i in range(20):
        _emit(
            tmp_path, f"p{i}", f"flat/p{i}.py", "code", 0.20,
            label={"event_type": "tool_use", "action": "Edit", "source": "hook"},
        )
    # Thin subkind data — below the threshold.
    for i in range(3):
        _emit(
            tmp_path, f"h{i}", f"api/h{i}.py", "code/handler", 0.20,
            label={"event_type": "tool_use", "action": "Edit", "source": "hook"},
        )
    result = tune(tmp_path, config=cfg)
    assert any(f.kind == "code/handler" for f in result.fits)
    assert "code/handler" not in result.learned_thresholds
    assert "code" in result.learned_thresholds


def test_end_to_end_subkind_threshold_round_trips(tmp_path: Path):
    cfg = TuneConfig(min_labels_per_kind=5, prior_sigma=0.15)
    # Drive code/handler T down via Edit labels at low cosine
    for i in range(15):
        _emit(
            tmp_path, f"h{i}", f"api/h{i}.py", "code/handler", 0.18,
            label={"event_type": "tool_use", "action": "Edit", "source": "hook"},
        )
    result = tune(tmp_path, config=cfg)
    apply_tune(tmp_path, result)
    # Reload via the loader and check it picks up code/handler at the fitted value.
    from repoctx.config_loader import load_repo_config
    loaded = load_repo_config(tmp_path)
    learned_handler = result.learned_thresholds.get("code/handler")
    assert learned_handler is not None
    assert abs(loaded.qualify_threshold_for("code", "handler") - learned_handler) < 1e-6


def test_apply_writes_hierarchical_keys(tmp_path: Path):
    cfg = TuneConfig(min_labels_per_kind=5, prior_sigma=0.15)
    for i in range(10):
        _emit(
            tmp_path, f"h{i}", f"api/h{i}.py", "code/handler", 0.18,
            label={"event_type": "tool_use", "action": "Edit", "source": "hook"},
        )
    result = tune(tmp_path, config=cfg)
    apply_tune(tmp_path, result)
    cfg_path = tmp_path / ".repoctx" / "config.json"
    payload = json.loads(cfg_path.read_text())
    qkeys = payload["learned"]["embedding_qualify_thresholds"]
    assert "code/handler" in qkeys
