"""Phase 3 model: fit per-kind retrieval thresholds from feedback events.

What this is, plainly:
  For each kind k, we have a stream of observations ``(embedding_score, label,
  source, age)`` from the feedback log. The retriever currently admits a hit
  iff its embedding cosine ≥ T_k. We want to pick T_k* that maximizes a
  precision/recall-balanced objective on the observed labels — but with a
  *strong prior* on the configured default, because per-repo data is thin.

What this is NOT:
  - A learned encoder / cross-encoder. Those are Phase 5.
  - A reranker. Same.
  - A black-box ML model. The tuner produces a single scalar per kind so
    its output is inspectable and trivial to roll back (delete the
    ``learned`` block in ``.repoctx/config.json``).

Approach (conservative, debuggable):

1. For each label, compute a *signed weight*: positive labels contribute +w,
   noise contributes -w. The weight is the product of provenance weight
   (hook=1.0, git=0.8, self_report.informed_edit=0.9, informed_context=0.7,
   noise=1.0, hook-Read-only=0.3) and a time-decay factor (exponential,
   half-life 30 days by default).

2. For each kind, build a candidate-threshold grid (0.05 step from 0.0 to
   0.95) and score each threshold T by:

       score(T) = sum over labels[k] of weight_i * sign(label_i) * (1 if
                    embedding_score_i >= T else -1)

   This is a margin-style objective: a positive label above T contributes
   positively (we admitted it correctly), a positive below T contributes
   negatively (we filtered out something the agent valued), a noise label
   above T contributes negatively (we shipped something useless), and a
   noise below T contributes positively (we correctly filtered it).

3. Combine with a Gaussian log-prior centered on the configured default
   threshold for that kind (default σ = 0.07 → ~95% of mass within ±0.14
   of the prior). The prior dominates when the data is thin and yields
   gracefully as evidence accumulates.

4. The argmax (over the grid) of ``data_score + prior_log_score`` is T_k*.
   We then write T_k* into ``.repoctx/config.json`` under ``learned``.

Limitations to be honest about (see plan's known-limitations section):
  - Exposure bias: we only see labels for what the bundle admitted. The
    exploration budget in the retriever ([retriever.py](retriever.py))
    mitigates by occasionally including sub-threshold candidates so we
    can observe what the threshold is filtering.
  - Self-attribution: ``informed_context`` is graded by the LLM and noisy.
  - Counterfactual blindness: we can't tell whether a *different* bundle
    would have done better. Mitigated by logging per-item scores so
    Phase 5 can do propensity-weighted reranking.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from repoctx.config import DEFAULT_CONFIG
from repoctx.eval import iter_labels_for_tuner
from repoctx.feedback_log import read_events
from repoctx.subkinds import parent_kind as _parent_kind

logger = logging.getLogger(__name__)

# Provenance weights. See plan Phase 3 "Provenance weighting".
PROVENANCE_WEIGHTS: dict[tuple[str, str | None], float] = {
    # (source, relevance_or_action)
    ("hook", "Edit"): 1.0,
    ("hook", "Write"): 1.0,
    ("hook", "MultiEdit"): 1.0,
    ("hook", "Read"): 0.3,  # curiosity, not relevance (only used for unlabeled Reads)
    ("git", "Edit"): 0.8,
    ("self_report", "informed_edit"): 0.9,
    ("self_report", "informed_context"): 0.7,
    ("self_report", "noise"): 1.0,
}

# Default tuner config — exposed via TuneConfig so tests / callers can pin.
THRESHOLD_GRID = [round(0.05 * i, 2) for i in range(1, 19)]  # 0.05 .. 0.90
DEFAULT_HALF_LIFE_DAYS = 30.0
DEFAULT_PRIOR_SIGMA = 0.07


@dataclass(slots=True)
class TuneConfig:
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS
    prior_sigma: float = DEFAULT_PRIOR_SIGMA
    min_labels_per_kind: int = 10
    threshold_grid: tuple[float, ...] = tuple(THRESHOLD_GRID)


@dataclass(slots=True)
class FitResult:
    kind: str
    prior_threshold: float
    fitted_threshold: float
    label_count: int
    positive_weight: float
    noise_weight: float
    confidence: str  # "prior_only" | "weak" | "strong"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "prior_threshold": round(self.prior_threshold, 4),
            "fitted_threshold": round(self.fitted_threshold, 4),
            "label_count": self.label_count,
            "positive_weight": round(self.positive_weight, 3),
            "noise_weight": round(self.noise_weight, 3),
            "confidence": self.confidence,
        }


@dataclass(slots=True)
class TuneResult:
    fits: list[FitResult]
    learned_thresholds: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "fits": [f.to_dict() for f in self.fits],
            "learned_thresholds": {k: round(v, 4) for k, v in self.learned_thresholds.items()},
        }


def tune(
    repo_root: str | Path,
    *,
    config: TuneConfig | None = None,
    now: datetime | None = None,
    base_thresholds: dict[str, float] | None = None,
) -> TuneResult:
    """Fit a per-kind threshold from the feedback log; return what would be written.

    Does not write to disk — use :func:`apply_tune` to persist. Callers that
    want a dry-run inspection just call ``tune()`` and print the result.
    """
    cfg = config or TuneConfig()
    repo = Path(repo_root)
    now_dt = now or datetime.now(timezone.utc)

    base = base_thresholds or dict(DEFAULT_CONFIG.embedding_qualify_thresholds)

    # Pull per-row labels with their scores.
    rows = list(iter_labels_for_tuner(repo))
    # And the event timestamps for time-decay weighting. iter_labels_for_tuner
    # doesn't carry timestamps (it dedupes labels), so we build a side map.
    label_ts = _label_timestamps(repo)

    by_full_kind: dict[str, list[tuple[float, str, str | None, str | None, float]]] = {}
    for row in rows:
        full = row["kind"]  # may be "code" or "code/handler" — the bundle_emitted event carries the full key
        emb_score = float(row["embedding_score"])
        label = row["label"]
        source = row.get("source")
        action_or_rel = row.get("relevance") or row.get("action")
        ts = label_ts.get((row["bundle_id"], row["path"]))
        age_days = _age_days(ts, now_dt) if ts else 0.0
        weight = _label_weight(label, source, action_or_rel, age_days, half_life_days=cfg.half_life_days)
        by_full_kind.setdefault(full, []).append((emb_score, label, source, action_or_rel, weight))

    # Two-pass hierarchical fit:
    #   Pass 1 fits each parent kind on *all* its labels (subkinds folded in).
    #          The result becomes the prior for sub-cells in pass 2 — so the
    #          subkind is shrunk toward its parent's evidence, and the parent
    #          is shrunk toward the configured default. This is the standard
    #          hierarchical-Bayes shape: information flows from data-rich
    #          cells (parents) to data-poor ones (subkinds).
    #   Pass 2 fits each observed subkind cell with the parent's fitted
    #          threshold as the prior mean.
    parent_kinds = {k for k in by_full_kind if "/" not in k}
    for fk in by_full_kind:
        if "/" in fk:
            parent_kinds.add(_parent_kind(fk))
    # Also fit parents that appear in the base map even if no labels seen yet —
    # so the output always lists every default kind for inspectability.
    parent_kinds.update(k for k in base if k != "_default")

    fits: list[FitResult] = []
    learned: dict[str, float] = {}
    parent_fits: dict[str, float] = {}

    # Pass 1 — parent kinds
    for kind in sorted(parent_kinds):
        prior_t = base.get(kind, base.get("_default", 0.3))
        parent_rows = []
        for fk, rs in by_full_kind.items():
            if _parent_kind(fk) == kind:
                parent_rows.extend(rs)
        fit = _fit_one_cell(
            cell_key=kind,
            rows=parent_rows,
            prior_t=prior_t,
            cfg=cfg,
        )
        fits.append(fit)
        learned[kind] = fit.fitted_threshold
        parent_fits[kind] = fit.fitted_threshold

    # Pass 2 — subkind cells
    for fk in sorted(by_full_kind):
        if "/" not in fk:
            continue
        parent = _parent_kind(fk)
        prior_t = parent_fits.get(parent, base.get(parent, base.get("_default", 0.3)))
        fit = _fit_one_cell(
            cell_key=fk,
            rows=by_full_kind[fk],
            prior_t=prior_t,
            cfg=cfg,
        )
        fits.append(fit)
        # Write a learned entry iff the subkind cell actually fit (had
        # enough labels). Cells in ``prior_only`` confidence inherit the
        # parent's value via the loader's fallback chain — no need to
        # bake them into the config and clutter it.
        if fit.confidence != "prior_only":
            learned[fk] = fit.fitted_threshold

    return TuneResult(fits=fits, learned_thresholds=learned)


def _fit_one_cell(
    *,
    cell_key: str,
    rows: list[tuple[float, str, str | None, str | None, float]],
    prior_t: float,
    cfg: TuneConfig,
) -> FitResult:
    """Run the MAP grid search for one cell. Returns the prior when thin."""
    labeled = [r for r in rows if r[1] in ("positive", "noise")]
    pos_w = sum(r[4] for r in labeled if r[1] == "positive")
    noise_w = sum(r[4] for r in labeled if r[1] == "noise")
    if len(labeled) < cfg.min_labels_per_kind:
        return FitResult(
            kind=cell_key,
            prior_threshold=prior_t,
            fitted_threshold=prior_t,
            label_count=len(labeled),
            positive_weight=pos_w,
            noise_weight=noise_w,
            confidence="prior_only",
        )

    best_t = prior_t
    best_score = float("-inf")
    for t in cfg.threshold_grid:
        data_score = _data_score(labeled, t)
        prior_score = _gaussian_log_prior(t, mean=prior_t, sigma=cfg.prior_sigma)
        total = data_score + prior_score
        if total > best_score:
            best_score = total
            best_t = t

    confidence = "strong" if len(labeled) >= 50 else "weak"
    return FitResult(
        kind=cell_key,
        prior_threshold=prior_t,
        fitted_threshold=best_t,
        label_count=len(labeled),
        positive_weight=pos_w,
        noise_weight=noise_w,
        confidence=confidence,
    )


def _data_score(
    labeled: list[tuple[float, str, str | None, str | None, float]],
    threshold: float,
) -> float:
    """Margin-style objective at *threshold*. See module docstring."""
    score = 0.0
    for emb_score, label, _src, _aor, weight in labeled:
        above = emb_score >= threshold
        if label == "positive":
            score += weight if above else -weight
        elif label == "noise":
            score += -weight if above else weight
    return score


def _gaussian_log_prior(t: float, *, mean: float, sigma: float) -> float:
    # Unnormalized log-density — the constant doesn't matter for argmax.
    return -((t - mean) ** 2) / (2 * sigma ** 2)


def _label_weight(
    label: str,
    source: str | None,
    action_or_rel: str | None,
    age_days: float,
    *,
    half_life_days: float,
) -> float:
    if label == "unlabeled":
        return 0.0
    prov_key = (source or "", action_or_rel or "")
    prov = PROVENANCE_WEIGHTS.get(prov_key, 0.5)
    if half_life_days > 0 and age_days > 0:
        decay = math.exp(-math.log(2) * age_days / half_life_days)
    else:
        decay = 1.0
    return prov * decay


def _label_timestamps(repo_root: Path) -> dict[tuple[str, str], datetime]:
    """Build a (bundle_id, path) → latest-label-timestamp map.

    Used for time-decay weighting. Picks the *latest* timestamp across all
    label events for a (bundle, path) so recent re-confirmations refresh
    decay; the bundle_emitted timestamp is not used (we want the label
    age, not the bundle age).
    """
    out: dict[tuple[str, str], datetime] = {}
    label_events = ("tool_use", "self_report", "git_edit")
    for evt in read_events(repo_root):
        if evt.get("event_type") not in label_events:
            continue
        bid = evt.get("bundle_id")
        path = evt.get("path")
        ts_str = evt.get("event_time")
        if not (isinstance(bid, str) and isinstance(path, str) and isinstance(ts_str, str)):
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        key = (bid, path)
        prev = out.get(key)
        if prev is None or ts > prev:
            out[key] = ts
    return out


def _age_days(ts: datetime, now: datetime) -> float:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    delta = now - ts
    return max(0.0, delta.total_seconds() / 86400.0)


def apply_tune(repo_root: str | Path, result: TuneResult) -> Path:
    """Write the fitted thresholds into ``<repo>/.repoctx/config.json``.

    Merges into a ``learned`` block so user-set values at the root stay
    visible and can override (per the loader's precedence). Preserves any
    other top-level keys (e.g. ``feedback_enabled``).
    """
    import json

    repo = Path(repo_root)
    cfg_path = repo / ".repoctx" / "config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {}
    if cfg_path.exists():
        try:
            existing = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                payload = existing
        except json.JSONDecodeError:
            logger.warning("Overwriting unparseable %s", cfg_path)

    learned = payload.setdefault("learned", {})
    if not isinstance(learned, dict):
        learned = {}
        payload["learned"] = learned
    learned["embedding_qualify_thresholds"] = {
        k: round(v, 4) for k, v in result.learned_thresholds.items()
    }
    cfg_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return cfg_path
