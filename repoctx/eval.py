"""Evaluation harness: aggregate feedback-log events into per-kind metrics.

Powers ``repoctx eval`` and feeds the Phase 3 tuner with the join table it
needs. The "precision" / "recall" terminology here is loose — we're not
measuring retrieval against a ground-truth label set, we're measuring
retrieval against *observed agent behavior*:

- **precision-ish** = of paths the retriever shipped in a bundle, what
  fraction earned any positive label (Edit, informed_edit, informed_context,
  or git_edit).
- **recall-ish** = of paths the agent ended up Read/Editing in the session,
  what fraction were in the bundle. The denominator is biased — files the
  agent found via grep or memory don't generalize — but a sudden drop here
  after a tune run is still a useful canary against over-tightening.
- **noise rate** = of bundle paths, what fraction were explicitly labeled
  ``noise`` via mark_used.

All metrics are computed per kind, plus an overall aggregate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from repoctx.feedback_log import read_events

POSITIVE_RELEVANCES = frozenset({"informed_edit", "informed_context"})
EDIT_ACTIONS = frozenset({"Edit", "Write", "MultiEdit"})


@dataclass(slots=True)
class KindStats:
    bundle_paths: int = 0
    positives: int = 0
    noise: int = 0
    # session-touched paths in this kind that weren't in any bundle
    misses: int = 0

    def precision(self) -> float:
        if self.bundle_paths == 0:
            return 0.0
        return self.positives / self.bundle_paths

    def noise_rate(self) -> float:
        if self.bundle_paths == 0:
            return 0.0
        return self.noise / self.bundle_paths

    def recall(self) -> float:
        # Loose recall: positives / (positives + misses). Both numerator
        # and denominator are agent-observed, so this is "of paths the
        # agent touched, fraction that were bundled".
        total = self.positives + self.misses
        if total == 0:
            return 0.0
        return self.positives / total

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle_paths": self.bundle_paths,
            "positives": self.positives,
            "noise": self.noise,
            "misses": self.misses,
            "precision": round(self.precision(), 4),
            "recall": round(self.recall(), 4),
            "noise_rate": round(self.noise_rate(), 4),
        }


@dataclass(slots=True)
class EvalReport:
    bundles: int = 0
    events_total: int = 0
    by_kind: dict[str, KindStats] = field(default_factory=dict)
    overall: KindStats = field(default_factory=KindStats)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundles": self.bundles,
            "events_total": self.events_total,
            "by_kind": {k: v.to_dict() for k, v in sorted(self.by_kind.items())},
            "overall": self.overall.to_dict(),
        }


def compute_eval(
    repo_root: str | Path,
    *,
    since_iso: str | None = None,
) -> EvalReport:
    """Aggregate feedback events into an :class:`EvalReport`.

    Bundle/path → kind comes from the bundle_emitted event's ranked_paths.
    Tool-use events outside any bundle contribute to misses (with kind
    inferred from file extension via the same logic the assembler uses).
    """
    report = EvalReport()
    # First pass: index bundle_emitted → {path: kind} and accumulate bundle path counts.
    bundle_path_kinds: dict[str, dict[str, str]] = {}
    # Track which (bundle_id, path) already received a positive label so a
    # mix of hook+self_report+git on the same path counts once.
    positives_seen: set[tuple[str, str]] = set()
    noise_seen: set[tuple[str, str]] = set()

    for evt in read_events(repo_root, since_iso=since_iso):
        report.events_total += 1
        et = evt.get("event_type")
        bid = evt.get("bundle_id")
        if et == "bundle_emitted":
            report.bundles += 1
            ranked = evt.get("ranked_paths") or []
            path_kinds: dict[str, str] = {}
            for entry in ranked:
                if not isinstance(entry, dict):
                    continue
                p = entry.get("path")
                k = entry.get("kind")
                if isinstance(p, str) and isinstance(k, str):
                    path_kinds[p] = k
                    stats = report.by_kind.setdefault(k, KindStats())
                    stats.bundle_paths += 1
                    report.overall.bundle_paths += 1
            if isinstance(bid, str) and bid:
                bundle_path_kinds[bid] = path_kinds

    # Second pass: attribute tool_use / self_report / git_edit to bundles.
    # We iterate again because event order isn't guaranteed (the log appends
    # in the order events fire, which can interleave with newer bundles).
    for evt in read_events(repo_root, since_iso=since_iso):
        et = evt.get("event_type")
        bid = evt.get("bundle_id")
        path = evt.get("path")
        if et == "tool_use":
            if not isinstance(path, str):
                continue
            if isinstance(bid, str) and bid in bundle_path_kinds and path in bundle_path_kinds[bid]:
                action = evt.get("action")
                if action in EDIT_ACTIONS:
                    _credit_positive(report, bundle_path_kinds[bid][path], bid, path, positives_seen)
                # Reads alone don't count as positives — only informed_context
                # via mark_used does (since the Read by itself is curiosity).
            else:
                # Touched but not in any matching bundle → miss.
                kind = _infer_kind_for_miss(path)
                stats = report.by_kind.setdefault(kind, KindStats())
                stats.misses += 1
                report.overall.misses += 1
        elif et == "self_report":
            if not isinstance(path, str) or not isinstance(bid, str):
                continue
            rel = evt.get("relevance")
            paths_in_bundle = bundle_path_kinds.get(bid, {})
            if path not in paths_in_bundle:
                continue
            kind = paths_in_bundle[path]
            if rel in POSITIVE_RELEVANCES:
                _credit_positive(report, kind, bid, path, positives_seen)
            elif rel == "noise":
                key = (bid, path)
                if key not in noise_seen:
                    noise_seen.add(key)
                    stats = report.by_kind.setdefault(kind, KindStats())
                    stats.noise += 1
                    report.overall.noise += 1
        elif et == "git_edit":
            if not isinstance(path, str) or not isinstance(bid, str):
                continue
            paths_in_bundle = bundle_path_kinds.get(bid, {})
            if path in paths_in_bundle:
                _credit_positive(report, paths_in_bundle[path], bid, path, positives_seen)

    return report


def _credit_positive(
    report: EvalReport,
    kind: str,
    bundle_id: str,
    path: str,
    seen: set[tuple[str, str]],
) -> None:
    key = (bundle_id, path)
    if key in seen:
        return
    seen.add(key)
    stats = report.by_kind.setdefault(kind, KindStats())
    stats.positives += 1
    report.overall.positives += 1


def _infer_kind_for_miss(path: str) -> str:
    """Classify a missed (edited-but-unbundled) path into ``kind/subkind``.

    Path-only inference: we don't have FileRecord context for paths that
    weren't in any bundle, and reading from disk per miss-event would be a
    syscall per row at eval time. Content-based detectors in the classifier
    are skipped (no content) so the fallback rules drive subkind.
    """
    from repoctx.config import DEFAULT_CONFIG
    from repoctx.subkinds import classify_subkind, full_kind

    suffix = Path(path).suffix.lower()
    if suffix in DEFAULT_CONFIG.config_extensions:
        kind = "config"
    elif suffix in (".md", ".mdc"):
        kind = "doc"
    elif any(marker in path for marker in DEFAULT_CONFIG.test_markers):
        kind = "test"
    else:
        name = Path(path).name
        if "test_" in name or "_test" in name:
            kind = "test"
        else:
            kind = "code"
    subkind = classify_subkind(kind, path, "")
    return full_kind(kind, subkind)


def iter_labels_for_tuner(
    repo_root: str | Path,
    *,
    since_iso: str | None = None,
) -> Iterable[dict[str, Any]]:
    """Yield join rows the Phase 3 tuner consumes.

    Each row: ``{bundle_id, path, kind, embedding_score, heuristic_score,
    label, source, action?, relevance?}``. ``label`` is one of ``positive``,
    ``noise``, ``unlabeled``. The tuner uses ``embedding_score`` as the
    independent variable to fit per-kind thresholds.
    """
    bundle_index: dict[str, dict[str, dict[str, Any]]] = {}
    for evt in read_events(repo_root, since_iso=since_iso):
        if evt.get("event_type") != "bundle_emitted":
            continue
        bid = evt.get("bundle_id")
        if not isinstance(bid, str):
            continue
        path_map: dict[str, dict[str, Any]] = {}
        for entry in evt.get("ranked_paths") or []:
            if not isinstance(entry, dict):
                continue
            p = entry.get("path")
            if isinstance(p, str):
                path_map[p] = entry
        bundle_index[bid] = path_map

    labels: dict[tuple[str, str], dict[str, Any]] = {}
    for evt in read_events(repo_root, since_iso=since_iso):
        et = evt.get("event_type")
        bid = evt.get("bundle_id")
        path = evt.get("path")
        if not isinstance(bid, str) or not isinstance(path, str):
            continue
        key = (bid, path)
        if et == "self_report":
            rel = evt.get("relevance")
            if rel in POSITIVE_RELEVANCES:
                labels[key] = {"label": "positive", "source": "self_report", "relevance": rel}
            elif rel == "noise":
                labels[key] = {"label": "noise", "source": "self_report", "relevance": rel}
        elif et == "tool_use":
            action = evt.get("action")
            if action in EDIT_ACTIONS and key not in labels:
                labels[key] = {"label": "positive", "source": "hook", "action": action}
        elif et == "git_edit":
            if key not in labels:
                labels[key] = {"label": "positive", "source": "git", "action": "Edit"}

    for bid, path_map in bundle_index.items():
        for path, entry in path_map.items():
            label_info = labels.get((bid, path), {"label": "unlabeled", "source": None})
            yield {
                "bundle_id": bid,
                "path": path,
                "kind": entry.get("kind", "code"),
                "embedding_score": float(entry.get("embedding_score") or 0.0),
                "heuristic_score": float(entry.get("heuristic_score") or 0.0),
                "score": float(entry.get("score") or 0.0),
                **label_info,
            }
