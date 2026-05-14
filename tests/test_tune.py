"""Tests for the Phase 3 tuner."""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from repoctx.config import DEFAULT_CONFIG
from repoctx.config_loader import load_repo_config
from repoctx.feedback_log import append_event
from repoctx.retriever import _maybe_explore
from repoctx.models import RankedPath
from repoctx.tune import (
    PROVENANCE_WEIGHTS,
    TuneConfig,
    apply_tune,
    tune,
)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _emit_bundle_with_label(
    repo: Path,
    bundle_id: str,
    path: str,
    kind: str,
    emb_score: float,
    *,
    label_event: dict | None = None,
    ts: datetime | None = None,
) -> None:
    ts = ts or datetime.now(timezone.utc)
    append_event(repo, {
        "event_type": "bundle_emitted",
        "bundle_id": bundle_id,
        "ranked_paths": [{
            "path": path, "kind": kind, "score": emb_score,
            "embedding_score": emb_score, "heuristic_score": 0.0,
        }],
        "event_time": _iso(ts),
    })
    if label_event is not None:
        evt = dict(label_event)
        evt["bundle_id"] = bundle_id
        evt["path"] = path
        evt["event_time"] = _iso(ts + timedelta(seconds=10))
        append_event(repo, evt)


def test_tune_falls_back_to_prior_when_data_thin(tmp_path: Path):
    # Fewer than min_labels_per_kind → fitted == prior, confidence prior_only.
    _emit_bundle_with_label(
        tmp_path, "b1", "a.py", "code", 0.5,
        label_event={"event_type": "tool_use", "action": "Edit", "source": "hook"},
    )
    result = tune(tmp_path)
    code_fit = next(f for f in result.fits if f.kind == "code")
    assert code_fit.fitted_threshold == code_fit.prior_threshold
    assert code_fit.confidence == "prior_only"


def test_tune_lowers_threshold_when_positives_below_default(tmp_path: Path):
    # All positive labels are at emb_score ≈ 0.20 — below the default 0.30
    # threshold. With enough labels the data score pulls T down.
    cfg = TuneConfig(min_labels_per_kind=5, prior_sigma=0.15)
    for i in range(20):
        _emit_bundle_with_label(
            tmp_path, f"b{i}", f"src/a{i}.py", "code", 0.20,
            label_event={"event_type": "tool_use", "action": "Edit", "source": "hook"},
        )
    result = tune(tmp_path, config=cfg)
    code_fit = next(f for f in result.fits if f.kind == "code")
    assert code_fit.fitted_threshold < code_fit.prior_threshold
    assert code_fit.confidence in ("weak", "strong")


def test_tune_raises_threshold_when_noise_above_default(tmp_path: Path):
    # All noise labels are at emb_score ≈ 0.35 — just above the default 0.30
    # threshold. The data score pushes T up to filter them out.
    cfg = TuneConfig(min_labels_per_kind=5, prior_sigma=0.15)
    for i in range(20):
        _emit_bundle_with_label(
            tmp_path, f"b{i}", f"src/a{i}.py", "code", 0.35,
            label_event={
                "event_type": "self_report",
                "relevance": "noise",
                "source": "self_report",
            },
        )
    result = tune(tmp_path, config=cfg)
    code_fit = next(f for f in result.fits if f.kind == "code")
    assert code_fit.fitted_threshold > code_fit.prior_threshold


def test_provenance_weighting_self_report_noise_outweighs_hook_read(tmp_path: Path):
    # Read-only hook events have weight 0.3; noise has weight 1.0. With
    # equal counts at the same score, noise should dominate the fit.
    cfg = TuneConfig(min_labels_per_kind=2, prior_sigma=0.15)
    for i in range(10):
        # hook Read at high cosine — weak positive signal (weight 0.3)
        _emit_bundle_with_label(
            tmp_path, f"r{i}", f"r{i}.py", "code", 0.40,
            label_event={"event_type": "tool_use", "action": "Read", "source": "hook"},
        )
        # mark_used noise at the same cosine — strong negative (weight 1.0)
        _emit_bundle_with_label(
            tmp_path, f"n{i}", f"n{i}.py", "code", 0.40,
            label_event={"event_type": "self_report", "relevance": "noise", "source": "self_report"},
        )
    result = tune(tmp_path, config=cfg)
    code_fit = next(f for f in result.fits if f.kind == "code")
    # Noise has higher weight + Reads aren't credited as positives in the
    # eval label join, so the tuner should push T up past 0.40 to filter
    # the noise out.
    assert code_fit.fitted_threshold > 0.40


def test_apply_tune_writes_learned_block(tmp_path: Path):
    cfg = TuneConfig(min_labels_per_kind=2, prior_sigma=0.15)
    for i in range(15):
        _emit_bundle_with_label(
            tmp_path, f"b{i}", f"a{i}.py", "code", 0.2,
            label_event={"event_type": "tool_use", "action": "Edit", "source": "hook"},
        )
    result = tune(tmp_path, config=cfg)
    apply_tune(tmp_path, result)

    cfg_path = tmp_path / ".repoctx" / "config.json"
    assert cfg_path.exists()
    payload = json.loads(cfg_path.read_text())
    assert "learned" in payload
    assert "embedding_qualify_thresholds" in payload["learned"]
    assert "code" in payload["learned"]["embedding_qualify_thresholds"]


def test_apply_tune_preserves_user_overrides(tmp_path: Path):
    # User wrote a root-level override; apply_tune must not nuke it.
    cfg_dir = tmp_path / ".repoctx"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text(json.dumps({
        "embedding_qualify_thresholds": {"code": 0.42},
        "feedback_enabled": False,
    }))
    # Empty log → prior_only fit, writes learned block but doesn't disturb root.
    result = tune(tmp_path)
    apply_tune(tmp_path, result)
    payload = json.loads((cfg_dir / "config.json").read_text())
    assert payload["embedding_qualify_thresholds"] == {"code": 0.42}
    assert payload["feedback_enabled"] is False
    assert "learned" in payload


def test_learned_block_is_picked_up_by_loader(tmp_path: Path):
    # End-to-end: tune → apply → load → retriever sees the fitted value.
    cfg = TuneConfig(min_labels_per_kind=2, prior_sigma=0.15)
    for i in range(15):
        _emit_bundle_with_label(
            tmp_path, f"b{i}", f"a{i}.py", "code", 0.2,
            label_event={"event_type": "tool_use", "action": "Edit", "source": "hook"},
        )
    result = tune(tmp_path, config=cfg)
    apply_tune(tmp_path, result)
    loaded = load_repo_config(tmp_path)
    fitted_code = result.learned_thresholds["code"]
    assert loaded.qualify_threshold_for("code") == pytest.approx(fitted_code, abs=1e-4)


def test_time_decay_reduces_old_label_weight(tmp_path: Path):
    cfg = TuneConfig(min_labels_per_kind=2, prior_sigma=0.15, half_life_days=1.0)
    now = datetime.now(timezone.utc)
    # Recent label
    _emit_bundle_with_label(
        tmp_path, "recent", "r.py", "code", 0.5,
        label_event={"event_type": "tool_use", "action": "Edit", "source": "hook"},
        ts=now,
    )
    # Stale label (10 half-lives ago → weight ~ 0.001)
    _emit_bundle_with_label(
        tmp_path, "old", "o.py", "code", 0.5,
        label_event={"event_type": "tool_use", "action": "Edit", "source": "hook"},
        ts=now - timedelta(days=10),
    )
    result = tune(tmp_path, config=cfg, now=now)
    code_fit = next(f for f in result.fits if f.kind == "code")
    # Total positive weight should be ~1.0 (recent) + tiny (old), not ~2.0.
    assert code_fit.positive_weight < 1.2
    assert code_fit.positive_weight > 0.9


def test_provenance_weights_match_plan(tmp_path: Path):
    # Sanity check that the weights documented in the plan are what's wired.
    assert PROVENANCE_WEIGHTS[("hook", "Edit")] == 1.0
    assert PROVENANCE_WEIGHTS[("git", "Edit")] == 0.8
    assert PROVENANCE_WEIGHTS[("self_report", "informed_edit")] == 0.9
    assert PROVENANCE_WEIGHTS[("self_report", "informed_context")] == 0.7
    assert PROVENANCE_WEIGHTS[("self_report", "noise")] == 1.0
    assert PROVENANCE_WEIGHTS[("hook", "Read")] == 0.3


# --- exploration budget ----------------------------------------------------


def _rp(path: str, emb: float) -> RankedPath:
    return RankedPath(
        path=path, reason="", score=emb,
        snippet=None, heuristic_score=0.0, embedding_score=emb,
    )


def test_exploration_returns_top_when_no_sub_threshold():
    top = [_rp("a.py", 0.6)]
    out = _maybe_explore(top, [], epsilon=1.0)
    assert out == top


def test_exploration_returns_top_when_epsilon_zero():
    top = [_rp("a.py", 0.6)]
    sub = [_rp("b.py", 0.2)]
    out = _maybe_explore(top, sub, epsilon=0.0)
    assert out == top


def test_exploration_appends_when_epsilon_one():
    top = [_rp("a.py", 0.6)]
    sub = [_rp("b.py", 0.2), _rp("c.py", 0.15)]
    # epsilon=1 means always fire.
    out = _maybe_explore(top, sub, epsilon=1.0)
    assert len(out) == 3
    # Higher-cosine probe comes first.
    assert out[1].path == "b.py"
    assert out[2].path == "c.py"


def test_exploration_caps_at_two_probes():
    top = [_rp("top.py", 0.6)]
    sub = [_rp(f"s{i}.py", 0.1 + i * 0.01) for i in range(10)]
    out = _maybe_explore(top, sub, epsilon=1.0)
    assert len(out) == 1 + 2


def test_exploration_is_probabilistic(monkeypatch: pytest.MonkeyPatch):
    top = [_rp("a.py", 0.6)]
    sub = [_rp("b.py", 0.2)]
    # Force random() to return values that straddle epsilon.
    seq = iter([0.99, 0.01])
    monkeypatch.setattr(random, "random", lambda: next(seq))
    out1 = _maybe_explore(top, sub, epsilon=0.05)
    out2 = _maybe_explore(top, sub, epsilon=0.05)
    assert len(out1) == 1  # 0.99 >= 0.05 → no probe
    assert len(out2) == 2  # 0.01 < 0.05 → probe added
