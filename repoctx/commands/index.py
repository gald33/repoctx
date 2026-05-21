"""index, rebuild, and update subcommands."""

import argparse
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

logger = logging.getLogger(__name__)


def _build_and_save_index(
    repo: Path, *, incremental: bool = False, source: str = "origin-main",
) -> None:
    try:
        from repoctx.embeddings import build_index
    except ImportError:
        print("Embedding dependencies not installed. Run: pip install 'repoctx-mcp[embeddings]'", file=sys.stderr)
        raise SystemExit(1)

    from repoctx.index_location import migrate_legacy_index_if_needed, shared_embeddings_dir

    repo = repo.resolve()
    # Pull any pre-1.5 in-tree index up to the shared location first so an
    # incremental build splices onto it instead of re-embedding from scratch.
    migrate_legacy_index_if_needed(repo)
    try:
        record_store = build_index(repo, incremental=incremental, source=source)
    except ImportError as exc:
        print(f"{exc}", file=sys.stderr)
        raise SystemExit(1)
    emb_dir = shared_embeddings_dir(repo)
    record_store.save(emb_dir)
    unique_files = len({e.path for e in record_store.entries})
    mode = "incrementally" if incremental else "fully"
    built_from = record_store.source_meta.get("built_from", source)
    base = record_store.source_meta.get("base_ref")
    suffix = f" from {base}" if base else ""
    print(
        f"Indexed {len(record_store)} chunks across {unique_files} files "
        f"({mode}, {built_from}{suffix}) → {emb_dir}"
    )


def _refresh_index(repo: Path) -> None:
    try:
        from repoctx.embeddings import refresh_base_index
    except ImportError:
        print("Embedding dependencies not installed. Run: pip install 'repoctx-mcp[embeddings]'", file=sys.stderr)
        raise SystemExit(1)
    from repoctx.index_location import migrate_legacy_index_if_needed

    repo = repo.resolve()
    migrate_legacy_index_if_needed(repo)
    result = refresh_base_index(repo, force=True, fetch=True, embed=True, build_if_missing=True)
    print(json.dumps(result, indent=2))


# -- index --------------------------------------------------------------------

def _register_index(subparsers) -> None:
    idx = subparsers.add_parser("index", help="Build the embedding index for a repo")
    idx.add_argument("--repo", default=".", help="Repository root")
    idx.add_argument("--verbose", action="store_true")
    idx.add_argument(
        "--incremental",
        action="store_true",
        help=(
            "Only re-embed chunks whose content_hash changed since the previous "
            "index. Falls back to a full rebuild if the existing index is "
            "missing, schema-incompatible, or built with a different model or "
            "chunker config."
        ),
    )
    idx.add_argument(
        "--source",
        choices=("origin-main", "worktree"),
        default="origin-main",
        help=(
            "What to index. 'origin-main' (default) reads the tree from git "
            "objects at origin/main (landed work; branch-independent). "
            "'worktree' indexes the current working tree."
        ),
    )
    idx.add_argument(
        "--refresh",
        action="store_true",
        help=(
            "Fetch origin/main and re-embed the delta so the index tracks the "
            "current tip (builds from scratch if absent). Implies origin-main."
        ),
    )


def _run_index(args: argparse.Namespace) -> None:
    if getattr(args, "refresh", False):
        _refresh_index(Path(args.repo))
        return
    _build_and_save_index(
        Path(args.repo),
        incremental=getattr(args, "incremental", False),
        source=getattr(args, "source", "origin-main"),
    )


index_cmd = SimpleNamespace(NAME="index", register=_register_index, run=_run_index)


# -- rebuild ------------------------------------------------------------------

def _register_rebuild(subparsers) -> None:
    rb = subparsers.add_parser("rebuild", help="Delete and rebuild the embedding index")
    rb.add_argument("--repo", default=".", help="Repository root")
    rb.add_argument("--verbose", action="store_true")


def _run_rebuild(args: argparse.Namespace) -> None:
    import shutil

    from repoctx.index_location import legacy_embeddings_dir, shared_embeddings_dir

    repo = Path(args.repo).resolve()
    # Wipe both the shared and any lingering legacy in-tree index so rebuild
    # leaves exactly one index, at the shared location.
    for emb_dir in {shared_embeddings_dir(repo), legacy_embeddings_dir(repo)}:
        if emb_dir.exists():
            shutil.rmtree(emb_dir)
            logger.info("Removed existing index at %s", emb_dir)
    _build_and_save_index(repo)


rebuild_cmd = SimpleNamespace(NAME="rebuild", register=_register_rebuild, run=_run_rebuild)


# -- update -------------------------------------------------------------------

def _register_update(subparsers) -> None:
    upd = subparsers.add_parser(
        "update",
        help="Queue a file for re-embedding (debounced; see --immediate / --flush)",
    )
    upd.add_argument(
        "file",
        nargs="?",
        help="Relative path to queue. Omit when using --flush, --status, or --from-claude-hook.",
    )
    upd.add_argument("--repo", default=".", help="Repository root")
    upd.add_argument(
        "--immediate",
        action="store_true",
        help="Embed synchronously, bypassing the debounce queue",
    )
    upd.add_argument(
        "--flush",
        action="store_true",
        help="Flush every queued path now (no file argument needed)",
    )
    upd.add_argument(
        "--status",
        action="store_true",
        help="Print queue status as JSON and exit",
    )
    upd.add_argument(
        "--from-claude-hook",
        action="store_true",
        help="Read Claude Code PostToolUse JSON from stdin and queue the edited file",
    )
    upd.add_argument("--verbose", action="store_true")


def _run_update(args: argparse.Namespace) -> None:
    try:
        from repoctx.embeddings import (
            enqueue_for_update,
            flush_pending,
            pending_status,
            update_file_in_index,
        )
    except ImportError:
        print("Embedding dependencies not installed. Run: pip install 'repoctx-mcp[embeddings]'", file=sys.stderr)
        raise SystemExit(1)

    repo = Path(args.repo)

    if args.status:
        print(json.dumps(pending_status(repo_root=repo), indent=2))
        return

    if args.flush:
        n = flush_pending(repo_root=repo)
        print(f"Flushed {n} pending embedding update(s)")
        return

    if args.from_claude_hook:
        try:
            payload = json.load(sys.stdin)
        except json.JSONDecodeError as exc:
            print(f"Invalid JSON on stdin: {exc}", file=sys.stderr)
            raise SystemExit(1)
        target = _extract_claude_hook_path(payload)
        if not target:
            return
        result = enqueue_for_update(target, repo_root=repo)
        if result.get("flushed"):
            print(f"Queued {result['queued']} (auto-flushed {result['flushed']} files)")
        else:
            print(f"Queued {result['queued']}")
        return

    if not args.file:
        print("update: file argument required (or pass --flush / --status / --from-claude-hook)", file=sys.stderr)
        raise SystemExit(2)

    try:
        if args.immediate:
            update_file_in_index(args.file, repo_root=repo)
            print(f"Updated embedding for {args.file}")
        else:
            result = enqueue_for_update(args.file, repo_root=repo)
            if result.get("flushed"):
                print(f"Queued {result['queued']} (auto-flushed {result['flushed']} files)")
            else:
                print(f"Queued {result['queued']}")
    except (ImportError, FileNotFoundError) as exc:
        print(f"{exc}", file=sys.stderr)
        raise SystemExit(1)


def _extract_claude_hook_path(payload: dict) -> str | None:
    """Pull the edited file path out of a Claude Code PostToolUse hook payload.

    Claude Code passes JSON like ``{"tool_name": "Edit", "tool_input": {"file_path": "..."}}``
    on stdin. We accept a few shape variants defensively so the hook keeps
    working across hook-schema revisions.
    """
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    for key in ("file_path", "filePath", "path"):
        val = tool_input.get(key)
        if isinstance(val, str) and val.strip():
            return val
    val = payload.get("file_path") or payload.get("path")
    return val if isinstance(val, str) and val.strip() else None


update_cmd = SimpleNamespace(NAME="update", register=_register_update, run=_run_update)
