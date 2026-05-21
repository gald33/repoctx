"""Advisory lane — retrieval over committed branches ahead of origin/main.

The live-main lane catches *landed* work; this catches *in-flight* work: "is
someone already building this on another branch / where is the architecture
heading?". It is deliberately and strictly separated from authoritative
retrieval:

- Built into its own index (``<git-common-dir>/repoctx/advisory``).
- Opt-in: nothing is built until ``repoctx advisory-index`` (or the
  ``include_advisory`` bundle flag) is used.
- Every hit carries provenance (branch, commits-ahead, last-commit date, merge
  status) and is returned under a separate key — never folded into
  ``relevant_code`` / ``authority`` / ``constraints``.

Only *committed* branch tips are indexed (read from git objects). Sibling
worktrees' uncommitted/working-tree bytes are never read — they're the least
trustworthy state in the repo.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repoctx.config import (
    DEFAULT_CONFIG,
    DEFAULT_EMBEDDING_CONFIG,
    EmbeddingConfig,
    RepoCtxConfig,
)
from repoctx.git_state import _run_git
from repoctx.git_tree import iter_tree_blobs, read_blobs, resolve_base_ref
from repoctx.index_location import index_state_root
from repoctx.scanner import build_file_record, is_supported_path

logger = logging.getLogger(__name__)

ADVISORY_SUBDIR = "advisory"
ADVISORY_NAMESPACE = "advisory"


@dataclass(frozen=True)
class BranchInfo:
    name: str
    sha: str
    committer_date: int  # unix seconds
    commits_ahead: int


def advisory_index_dir(repo_root: str | Path) -> Path:
    return index_state_root(repo_root) / ADVISORY_SUBDIR


def _ref_exists(repo_root: Path, ref: str) -> bool:
    return bool(_run_git(repo_root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"))


def advisory_base_ref(repo_root: Path) -> str | None:
    """Prefer ``origin/main`` as the 'already-landed' baseline; else fall back."""
    if _ref_exists(repo_root, "origin/main"):
        return "origin/main"
    resolved = resolve_base_ref(repo_root)
    return resolved[0] if resolved else None


def _count_ahead(repo_root: Path, base_ref: str, ref: str) -> int:
    out = _run_git(repo_root, "rev-list", "--count", f"{base_ref}..{ref}")
    try:
        return int(out.strip()) if out else 0
    except ValueError:
        return 0


def _merged_branch_names(repo_root: Path, base_ref: str) -> set[str]:
    out = _run_git(repo_root, "branch", "--merged", base_ref, "--format=%(refname:short)")
    if not out:
        return set()
    return {ln.strip() for ln in out.splitlines() if ln.strip()}


def enumerate_advisory_branches(
    repo_root: str | Path, config: EmbeddingConfig = DEFAULT_EMBEDDING_CONFIG
) -> list[BranchInfo]:
    """Branches ahead of origin/main, recent, deduped, not merged.

    Filters: commits-ahead > 0, last commit within ``advisory_max_age_days``,
    deduped by tip sha (local ref preferred), merged branches excluded. The
    base ref itself and symbolic ``*/HEAD`` refs are skipped. Capped to
    ``advisory_max_branches`` (most recent first).
    """
    root = Path(repo_root).resolve()
    base_ref = advisory_base_ref(root)
    if base_ref is None:
        return []
    out = _run_git(
        root, "for-each-ref",
        "--format=%(refname:short)%09%(committerdate:unix)%09%(objectname)",
        "refs/heads", "refs/remotes",
    )
    if not out:
        return []
    merged = _merged_branch_names(root, base_ref)
    now = time.time()
    max_age = config.advisory_max_age_days * 86400
    seen_sha: set[str] = set()
    infos: list[BranchInfo] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        name, date_s, sha = parts
        if not name or name == base_ref or name.endswith("/HEAD"):
            continue
        if name in merged:
            continue
        try:
            date = int(date_s)
        except ValueError:
            date = 0
        if max_age > 0 and (now - date) > max_age:
            continue
        if _count_ahead(root, base_ref, name) <= 0:
            continue
        if sha in seen_sha:
            continue
        seen_sha.add(sha)
        infos.append(
            BranchInfo(
                name=name, sha=sha, committer_date=date,
                commits_ahead=_count_ahead(root, base_ref, name),
            )
        )
    infos.sort(key=lambda b: -b.committer_date)
    return infos[: config.advisory_max_branches]


def _branch_changed_files(repo_root: Path, base_ref: str, branch: str) -> list[str]:
    out = _run_git(repo_root, "diff", "--name-only", f"{base_ref}...{branch}")
    if not out:
        return []
    return [ln for ln in out.splitlines() if ln.strip()]


def build_advisory_index(
    repo_root: str | Path,
    config: EmbeddingConfig = DEFAULT_EMBEDDING_CONFIG,
    scan_config: RepoCtxConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Build the advisory index from qualifying branch tips. Returns a summary.

    Each indexed chunk is tagged ``namespace="advisory"`` with provenance in
    its metadata. Files are read from each branch's *committed* tree (git
    objects) — never from any worktree's working directory.
    """
    from repoctx.embeddings import (
        HAS_EMBEDDINGS,
        EmbeddingModel,
        _chunk_to_entry,
        _chunks_for_record,
        build_enriched_chunk_text,
    )

    if not HAS_EMBEDDINGS:
        return {"status": "deps_missing"}
    from repoctx.chunker import ChunkConfig
    from repoctx.vector_index import VectorIndex

    root = Path(repo_root).resolve()
    base_ref = advisory_base_ref(root)
    if base_ref is None:
        return {"status": "no_base"}
    branches = enumerate_advisory_branches(root, config)
    chunk_cfg = ChunkConfig()
    texts: list[str] = []
    entries = []
    per_branch: list[dict[str, Any]] = []
    for b in branches:
        changed = [
            p for p in _branch_changed_files(root, base_ref, b.name)
            if is_supported_path(p, scan_config)
        ][: config.advisory_max_files_per_branch]
        if not changed:
            continue
        wanted = set(changed)
        blobs = {p: sha for p, sha in iter_tree_blobs(root, b.name, scan_config) if p in wanted}
        contents = read_blobs(root, list(blobs.values()), scan_config.max_file_bytes)
        n_chunks = 0
        for path in changed:
            sha = blobs.get(path)
            if sha is None:  # deleted on this branch — nothing to index
                continue
            record = build_file_record(path, contents.get(sha, ""), root, scan_config)
            for c in _chunks_for_record(record, chunk_cfg):
                texts.append(build_enriched_chunk_text(record, c))
                entry = _chunk_to_entry(record, c)
                entry.namespace = ADVISORY_NAMESPACE
                entry.metadata.update(
                    {
                        "branch": b.name,
                        "commits_ahead": b.commits_ahead,
                        "last_commit_date": b.committer_date,
                        "merge_status": "open",  # ahead of base & not merged
                    }
                )
                entries.append(entry)
                n_chunks += 1
        per_branch.append(
            {"branch": b.name, "commits_ahead": b.commits_ahead, "chunks": n_chunks}
        )

    import numpy as _np

    if texts:
        model = EmbeddingModel(config)
        vectors = model.encode_documents(texts, show_progress=len(texts) > 64)
        dim = int(vectors.shape[1])
    else:
        # Nothing in-flight qualified — persist an empty index (so search
        # reports "ok, no hits" rather than "not built") without loading a model.
        vectors = _np.empty((0, 0), dtype=_np.float32)
        dim = 0
    index = VectorIndex(
        vectors=vectors,
        entries=entries,
        model_name=config.model_name,
        dimension=dim,
        source_meta={
            "lane": "advisory",
            "base_ref": base_ref,
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )
    index.save(advisory_index_dir(root))
    return {
        "status": "built",
        "base_ref": base_ref,
        "branches": per_branch,
        "branch_count": len(per_branch),
        "chunks": len(entries),
    }


def op_advisory_search(
    query: str,
    repo_root: str | Path = ".",
    *,
    top_k: int = 10,
    config: EmbeddingConfig = DEFAULT_EMBEDDING_CONFIG,
    snippet_chars: int = 500,
) -> dict[str, Any]:
    """Search the advisory lane. Returns an envelope with provenance-tagged hits.

    ``status`` is ``no_index`` (advisory lane not built — opt-in) /
    ``deps_missing`` / ``ok``. Results are explicitly labeled as advisory and
    must NEVER be treated as authoritative.
    """
    from repoctx.embeddings import HAS_EMBEDDINGS, EmbeddingModel
    from repoctx.vector_index import VectorIndex

    root = Path(repo_root).resolve()
    adv_dir = advisory_index_dir(root)
    base = {"status": "ok", "lane": "advisory", "repo": str(root), "results": []}
    if not HAS_EMBEDDINGS:
        return {**base, "status": "deps_missing",
                "message": "Embedding dependencies not installed."}
    try:
        index = VectorIndex.load(adv_dir)
    except Exception:  # noqa: BLE001 — missing/incompatible both mean "not built"
        return {
            **base, "status": "no_index",
            "message": (
                "Advisory lane not built (it is opt-in). Run "
                "`repoctx advisory-index` to index in-flight branches."
            ),
        }
    if top_k <= 0 or len(index) == 0:
        return base
    model = EmbeddingModel(config)
    qv = model.encode_query(query)
    scored = index.similarity_scores_by_id(qv, namespace=ADVISORY_NAMESPACE)
    results: list[dict[str, Any]] = []
    for path, score, entry in scored[:top_k]:
        meta = entry.metadata or {}
        results.append(
            {
                "path": path,
                "score": float(score),
                "branch": meta.get("branch"),
                "commits_ahead": meta.get("commits_ahead"),
                "last_commit_date": meta.get("last_commit_date"),
                "merge_status": meta.get("merge_status"),
                "start_line": int(meta.get("start_line", 1)),
                "end_line": int(meta.get("end_line", 1)),
                "enclosing_symbol": meta.get("enclosing_symbol"),
            }
        )
    return {**base, "results": results}


__all__ = [
    "BranchInfo",
    "advisory_index_dir",
    "build_advisory_index",
    "enumerate_advisory_branches",
    "op_advisory_search",
]
