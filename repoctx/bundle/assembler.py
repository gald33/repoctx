"""Bundle assembler — composes authority + retrieval + scope into a bundle.

Phase-1 skeleton: assembles authority + relevant code + scope/validation stubs
with the self-recall contract fully populated. Scope/validation/risk logic is
intentionally conservative; Phase 3–4 flesh them out.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from time import perf_counter
from typing import Any
from uuid import uuid4

from repoctx.authority.constraints import Constraint
from repoctx.authority.discovery import AuthorityProducer
from repoctx.authority.records import AuthorityLevel, AuthorityRecord
from repoctx.bundle.recall import before_finalize_checklist, uncertainty_rule, when_to_recall_repoctx
from repoctx.bundle.schema import (
    EditScope,
    GroundTruthBundle,
    RankedCodeRef,
    RiskNote,
    ValidationPlan,
)
from repoctx.config import DEFAULT_CONFIG, RepoCtxConfig
from repoctx.feedback_log import append_event
from repoctx.git_state import collect_state
from repoctx.models import RankedPath
from repoctx.retriever import get_task_context


def build_bundle(
    task: str,
    repo_root: str | Path = ".",
    config: RepoCtxConfig | None = None,
    *,
    max_authority_records: int = 12,
    max_code_refs: int = 10,
    embedding_scores: dict[str, float] | None = None,
    index_status: Any | None = None,
) -> GroundTruthBundle:
    """Assemble a ground-truth bundle for ``task``.

    When ``config`` is None, per-repo retrieval knobs are loaded from
    ``<repo_root>/.repoctx/config.json`` (falling back to defaults if absent).
    Callers wanting to bypass that — e.g., tests pinning to a known config —
    can pass ``DEFAULT_CONFIG`` explicitly.

    ``index_status`` (a ``RetrieverStatus``) lets the caller report exactly why
    embeddings are/aren't active. When omitted and no ``embedding_scores`` are
    injected, a cheap model-free probe runs so the bundle still tells the agent
    when retrieval has silently degraded to lexical.
    """
    started = perf_counter()
    repo_path = Path(repo_root).resolve()
    if config is None:
        from repoctx.config_loader import load_repo_config
        config = load_repo_config(repo_path)

    # Authority discovery.
    producer = AuthorityProducer(repo_path, config=config)
    authority_records = producer.build_authority_records()
    authority_records = _rank_authority(authority_records, task)
    authority_records = authority_records[:max_authority_records]
    constraints = _constraints_from_records(authority_records)

    # Relevant code via the existing pipeline.
    context = get_task_context(
        task=task,
        repo_root=repo_path,
        config=config,
        embedding_scores=embedding_scores,
    )
    relevant_code = [_ranked_to_ref(p) for p in context.relevant_files[:max_code_refs]]
    examples = [
        _ranked_to_ref(p)
        for p in context.relevant_docs
        if p.path.startswith("examples/")
    ]

    # Scope + validation (conservative Phase-1 heuristics).
    edit_scope = _compute_scope(context.relevant_files, authority_records, constraints)
    validation_plan = _compute_validation_plan(context.related_tests, constraints)

    bundle_id = uuid4().hex[:16]
    bundle = GroundTruthBundle(
        task_summary=task[:240],
        task_raw=task,
        id=bundle_id,
        authoritative_records=authority_records,
        constraints=constraints,
        relevant_code=relevant_code,
        examples=examples,
        edit_scope=edit_scope,
        validation_plan=validation_plan,
        risk_notes=_initial_risk_notes(edit_scope, constraints),
    )
    bundle.when_to_recall_repoctx = when_to_recall_repoctx(
        edit_scope=edit_scope, constraints=constraints
    )
    bundle.before_finalize_checklist = before_finalize_checklist(
        validation_plan=validation_plan, edit_scope=edit_scope, constraints=constraints
    )
    bundle.uncertainty_rule = uncertainty_rule(constraints)
    ranker = "embeddings" if embedding_scores else "lexical"
    bundle.metrics = {
        "authority_records": len(authority_records),
        "constraints": len(constraints),
        "relevant_code": len(relevant_code),
        "build_duration_ms": int((perf_counter() - started) * 1000),
        "ranker": ranker,
    }
    _attach_retrieval_provenance(bundle, repo_path, ranker, embedding_scores, index_status)
    scope_paths = list(edit_scope.allowed_paths) + list(edit_scope.related_paths) + list(edit_scope.protected_paths)
    bundle.staleness = collect_state(repo_path, scope_paths=scope_paths)
    _emit_bundle_event(repo_path, bundle, context_relevant_files=context.relevant_files,
                       context_relevant_docs=context.relevant_docs,
                       context_related_tests=context.related_tests)
    return bundle


def _emit_bundle_event(
    repo_path: Path,
    bundle: GroundTruthBundle,
    *,
    context_relevant_files: list[RankedPath],
    context_relevant_docs: list[RankedPath],
    context_related_tests: list[RankedPath],
) -> None:
    """Write the ``bundle_emitted`` feedback event for later attribution.

    Ranked paths include the *full* ranker output (files + docs + tests),
    each with its ``kind/subkind`` key and the per-component scores. The
    tuner uses these to fit per-(kind, subkind) thresholds; the assembler
    just propagates what the scanner already classified onto each
    :class:`~repoctx.models.RankedPath`. Best-effort: any failure is
    swallowed since feedback logging must never break the bundle path.
    """
    try:
        ranked_paths: list[dict[str, Any]] = []
        for rp in (*context_relevant_files, *context_relevant_docs, *context_related_tests):
            ranked_paths.append(_ranked_path_event_entry(rp))
        append_event(
            repo_path,
            {
                "event_type": "bundle_emitted",
                "bundle_id": bundle.id,
                "task_raw": bundle.task_raw,
                "ranked_paths": ranked_paths,
                "ranker": bundle.metrics.get("ranker", "lexical"),
                "source": "internal",
                "repo_root": str(repo_path),
            },
        )
    except Exception:  # noqa: BLE001 — feedback logging must never break retrieval
        import logging
        logging.getLogger(__name__).debug("Failed to emit bundle_emitted event", exc_info=True)


def _ranked_path_event_entry(rp: RankedPath) -> dict[str, Any]:
    from repoctx.subkinds import full_kind
    return {
        "path": rp.path,
        "kind": full_kind(rp.kind or "code", rp.subkind),
        "score": float(rp.score),
        "heuristic_score": float(rp.heuristic_score),
        "embedding_score": float(rp.embedding_score),
    }


# ---- internals --------------------------------------------------------------------


def _attach_retrieval_provenance(
    bundle: GroundTruthBundle,
    repo_path: Path,
    ranker: str,
    embedding_scores: dict[str, float] | None,
    index_status: Any | None,
) -> None:
    """Populate ``bundle.retrieval`` and, when degraded, ``bundle.warnings``.

    The whole point: a bundle that fell back to lexical because no embedding
    index exists must say so at the top level, not bury ``ranker: "lexical"``
    in ``metrics`` where nobody looks (the original silent-degradation bug).
    """
    status = index_status
    if status is None and embedding_scores is None:
        try:
            from repoctx.embeddings import probe_index_status

            status = probe_index_status(repo_path)
        except Exception:  # noqa: BLE001 — provenance must never break bundling
            status = None

    if embedding_scores:
        index_status_code = "ok"
    elif status is not None:
        index_status_code = status.status
    else:
        index_status_code = "unknown"

    bundle.retrieval = {
        "ranker": ranker,
        "embeddings_active": bool(embedding_scores),
        "index_status": index_status_code,
        "index_location": getattr(status, "index_dir", "") if status is not None else "",
    }
    # Warn only when retrieval is genuinely degraded (no usable index), not
    # merely when a light op chose lexical while an index exists.
    if not embedding_scores and status is not None and not status.ok:
        bundle.warnings.append(status.message)


def _rank_authority(records: list[AuthorityRecord], task: str) -> list[AuthorityRecord]:
    """Stable authority-first ordering; lexical overlap breaks ties."""
    task_tokens = {t.lower() for t in task.split() if len(t) > 2}

    def key(r: AuthorityRecord) -> tuple[int, int, str]:
        haystack = f"{r.title} {r.summary} {' '.join(r.tags)}".lower()
        overlap = -sum(1 for t in task_tokens if t in haystack)
        return (int(r.authority_level), overlap, r.path)

    return sorted(records, key=key)


def _constraints_from_records(records: list[AuthorityRecord]) -> list[Constraint]:
    """Phase-3: extract constraints via front-matter + bullet parsing + inline markers."""
    from repoctx.authority.extract import extract_constraints

    return extract_constraints(records)


def _compute_scope(
    relevant_files: list[RankedPath],
    authority_records: list[AuthorityRecord],
    constraints: list[Constraint],
) -> EditScope:
    # Dunder package files (__init__.py, __main__.py) rarely carry the
    # actual logic a task targets — they get demoted to related so the
    # allowed set reflects files an agent would actually edit.
    def _is_boilerplate(path: str) -> bool:
        name = PurePosixPath(path).name
        return name.startswith("__") and name.endswith(".py")

    primary = [p for p in relevant_files if not _is_boilerplate(p.path)]
    secondary = [p for p in relevant_files if _is_boilerplate(p.path)]
    allowed = sorted({p.path for p in primary[:6]})
    related = sorted({p.path for p in primary[6:]} | {p.path for p in secondary})
    protected: set[str] = set()
    for r in authority_records:
        if r.authority_level == AuthorityLevel.HARD:
            protected.add(r.path.split(":", 1)[0])
    for c in constraints:
        if c.severity == "hard":
            protected.update(c.applies_to_paths)
    rationale_parts = []
    if protected:
        rationale_parts.append(f"Protected by {len([c for c in constraints if c.severity == 'hard'])} hard constraints.")
    rationale_parts.append("Allowed paths derived from top-ranked relevant files.")
    return EditScope(
        allowed_paths=allowed,
        related_paths=related,
        protected_paths=sorted(protected),
        rationale=" ".join(rationale_parts),
    )


def _compute_validation_plan(
    related_tests: list[RankedPath], constraints: list[Constraint]
) -> ValidationPlan:
    tests = [p.path for p in related_tests]
    for c in constraints:
        for ref in c.validation_refs:
            # Validation refs of shape "test:<path>".
            if ref.startswith("test:"):
                tests.append(ref.split(":", 1)[1])
    tests = sorted(set(tests))
    commands: list[str] = []
    if any(t.startswith("tests/") or t.endswith(".py") for t in tests):
        commands.append("pytest -q " + " ".join(tests) if tests else "pytest -q")
    return ValidationPlan(
        commands=commands,
        tests=tests,
        contract_checks=[c.source_record_id for c in constraints if c.source_record_id.startswith("contract:")],
        invariants_to_verify=[c.id for c in constraints if c.severity == "hard"],
    )


def _initial_risk_notes(edit_scope: EditScope, constraints: list[Constraint]) -> list[RiskNote]:
    notes: list[RiskNote] = []
    if edit_scope.protected_paths:
        notes.append(
            RiskNote(
                risk="Repository contains protected paths.",
                why=f"{len(edit_scope.protected_paths)} path(s) are covered by hard authority; call risk_report before finalizing.",
                severity="guided",
                related_ids=[],
            )
        )
    hard_count = sum(1 for c in constraints if c.severity == "hard")
    if hard_count:
        notes.append(
            RiskNote(
                risk=f"{hard_count} hard constraint(s) in scope.",
                why="Hard constraints must not be violated; re-read each before editing.",
                severity="hard",
                related_ids=[c.id for c in constraints if c.severity == "hard"],
            )
        )
    return notes


def _ranked_to_ref(p: RankedPath) -> RankedCodeRef:
    return RankedCodeRef(path=p.path, reason=p.reason, score=p.score, snippet=p.snippet)


__all__ = ["build_bundle"]
