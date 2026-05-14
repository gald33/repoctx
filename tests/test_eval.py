"""Tests for the eval harness."""

from __future__ import annotations

from pathlib import Path

from repoctx.eval import compute_eval, iter_labels_for_tuner
from repoctx.feedback_log import append_event


def _emit_bundle(repo: Path, bundle_id: str, *paths_with_kinds_scores):
    ranked = [
        {"path": p, "kind": k, "score": s, "embedding_score": s, "heuristic_score": 0.0}
        for (p, k, s) in paths_with_kinds_scores
    ]
    append_event(repo, {
        "event_type": "bundle_emitted",
        "bundle_id": bundle_id,
        "ranked_paths": ranked,
    })


def test_empty_log_zero_stats(tmp_path: Path):
    report = compute_eval(tmp_path)
    assert report.bundles == 0
    assert report.overall.bundle_paths == 0
    assert report.overall.precision() == 0.0


def test_bundle_paths_counted_per_kind(tmp_path: Path):
    _emit_bundle(tmp_path, "b1", ("a.py", "code", 0.5), ("README.md", "doc", 0.4), ("pkg.json", "config", 0.3))
    report = compute_eval(tmp_path)
    assert report.bundles == 1
    assert report.by_kind["code"].bundle_paths == 1
    assert report.by_kind["doc"].bundle_paths == 1
    assert report.by_kind["config"].bundle_paths == 1
    assert report.overall.bundle_paths == 3


def test_edit_tool_use_credits_positive(tmp_path: Path):
    _emit_bundle(tmp_path, "b1", ("src/a.py", "code", 0.5))
    append_event(tmp_path, {
        "event_type": "tool_use",
        "bundle_id": "b1",
        "path": "src/a.py",
        "action": "Edit",
        "source": "hook",
    })
    report = compute_eval(tmp_path)
    assert report.by_kind["code"].positives == 1
    assert report.by_kind["code"].precision() == 1.0


def test_read_alone_does_not_credit_positive(tmp_path: Path):
    _emit_bundle(tmp_path, "b1", ("src/a.py", "code", 0.5))
    append_event(tmp_path, {
        "event_type": "tool_use",
        "bundle_id": "b1",
        "path": "src/a.py",
        "action": "Read",
        "source": "hook",
    })
    report = compute_eval(tmp_path)
    assert report.by_kind["code"].positives == 0


def test_self_report_informed_context_credits(tmp_path: Path):
    _emit_bundle(tmp_path, "b1", ("src/a.py", "code", 0.5))
    append_event(tmp_path, {
        "event_type": "self_report",
        "bundle_id": "b1",
        "path": "src/a.py",
        "relevance": "informed_context",
        "source": "self_report",
    })
    report = compute_eval(tmp_path)
    assert report.by_kind["code"].positives == 1


def test_self_report_noise_counts_as_noise(tmp_path: Path):
    _emit_bundle(tmp_path, "b1", ("src/a.py", "code", 0.5))
    append_event(tmp_path, {
        "event_type": "self_report",
        "bundle_id": "b1",
        "path": "src/a.py",
        "relevance": "noise",
        "source": "self_report",
    })
    report = compute_eval(tmp_path)
    assert report.by_kind["code"].noise == 1
    assert report.by_kind["code"].noise_rate() == 1.0


def test_git_edit_credits_positive(tmp_path: Path):
    _emit_bundle(tmp_path, "b1", ("src/a.py", "code", 0.5))
    append_event(tmp_path, {
        "event_type": "git_edit",
        "bundle_id": "b1",
        "path": "src/a.py",
        "action": "Edit",
        "source": "git",
    })
    report = compute_eval(tmp_path)
    assert report.by_kind["code"].positives == 1


def test_multiple_sources_for_same_path_count_once(tmp_path: Path):
    _emit_bundle(tmp_path, "b1", ("src/a.py", "code", 0.5))
    # All three sources fire for the same path — should count as 1 positive.
    append_event(tmp_path, {
        "event_type": "tool_use",
        "bundle_id": "b1",
        "path": "src/a.py",
        "action": "Edit",
        "source": "hook",
    })
    append_event(tmp_path, {
        "event_type": "git_edit",
        "bundle_id": "b1",
        "path": "src/a.py",
        "action": "Edit",
        "source": "git",
    })
    append_event(tmp_path, {
        "event_type": "self_report",
        "bundle_id": "b1",
        "path": "src/a.py",
        "relevance": "informed_edit",
        "source": "self_report",
    })
    report = compute_eval(tmp_path)
    assert report.by_kind["code"].positives == 1


def test_unattributed_tool_use_counts_as_miss(tmp_path: Path):
    _emit_bundle(tmp_path, "b1", ("src/a.py", "code/other", 0.5))
    # Agent edited src/z.py which isn't in any bundle — miss is bucketed by
    # the path-only classifier (no path conventions matched → code/other).
    append_event(tmp_path, {
        "event_type": "tool_use",
        "bundle_id": None,
        "path": "src/z.py",
        "action": "Edit",
        "source": "hook",
    })
    report = compute_eval(tmp_path)
    assert report.by_kind["code/other"].misses == 1


def test_miss_kind_inferred_from_extension(tmp_path: Path):
    append_event(tmp_path, {
        "event_type": "tool_use",
        "bundle_id": None,
        "path": "settings.json",
        "action": "Edit",
        "source": "hook",
    })
    report = compute_eval(tmp_path)
    # settings.json → config (extension) → 'other' subkind (not a known build/lint/ci name)
    assert report.by_kind["config/other"].misses == 1


def test_recall_includes_misses_in_denominator(tmp_path: Path):
    # Place the positive in the same hierarchical bucket the miss will land in
    # so the recall denominator (positives + misses) accumulates in one cell.
    _emit_bundle(tmp_path, "b1", ("src/a.py", "code/other", 0.5))
    append_event(tmp_path, {
        "event_type": "tool_use",
        "bundle_id": "b1",
        "path": "src/a.py",
        "action": "Edit",
        "source": "hook",
    })
    append_event(tmp_path, {
        "event_type": "tool_use",
        "bundle_id": None,
        "path": "src/z.py",
        "action": "Edit",
        "source": "hook",
    })
    report = compute_eval(tmp_path)
    # 1 positive, 1 miss in the same code/other bucket → recall = 1/2
    assert report.by_kind["code/other"].recall() == 0.5


def test_iter_labels_yields_labeled_and_unlabeled(tmp_path: Path):
    _emit_bundle(tmp_path, "b1", ("a.py", "code", 0.6), ("b.py", "code", 0.4))
    append_event(tmp_path, {
        "event_type": "self_report",
        "bundle_id": "b1",
        "path": "a.py",
        "relevance": "informed_edit",
    })
    rows = list(iter_labels_for_tuner(tmp_path))
    assert len(rows) == 2
    labels = {r["path"]: r["label"] for r in rows}
    assert labels == {"a.py": "positive", "b.py": "unlabeled"}


def test_iter_labels_self_report_overrides_hook(tmp_path: Path):
    # If both hook (Edit) and self_report (noise) fire for the same path,
    # self_report wins — the LLM judge is more authoritative for explicit noise.
    _emit_bundle(tmp_path, "b1", ("a.py", "code", 0.5))
    append_event(tmp_path, {
        "event_type": "tool_use",
        "bundle_id": "b1",
        "path": "a.py",
        "action": "Edit",
        "source": "hook",
    })
    append_event(tmp_path, {
        "event_type": "self_report",
        "bundle_id": "b1",
        "path": "a.py",
        "relevance": "noise",
    })
    rows = {r["path"]: r["label"] for r in iter_labels_for_tuner(tmp_path)}
    assert rows["a.py"] == "noise"
