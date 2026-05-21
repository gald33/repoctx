"""Protocol-v2 operations: bundle, authority, scope, validate-plan, risk-report,
refresh, detect-changes, semantic-search."""

import argparse
import json
from types import SimpleNamespace

NAME_TO_CMD = {}


# -- bundle -------------------------------------------------------------------

def _register_bundle(subparsers) -> None:
    b = subparsers.add_parser("bundle", help="Build the ground-truth bundle for a task (v2)")
    b.add_argument("task", help="Task description")
    b.add_argument("--repo", default=".", help="Repository root")
    b.add_argument("--full", action="store_true", help="Include full authority text")
    b.add_argument(
        "--include-advisory",
        action="store_true",
        help="Attach advisory-lane hits (in-flight branches) under a separate key",
    )
    b.add_argument("--format", choices=("json", "markdown"), default="json")


def _run_bundle(args: argparse.Namespace) -> None:
    if getattr(args, "format", "json") == "markdown":
        from repoctx.bundle import build_bundle, render_bundle_markdown

        bundle = build_bundle(args.task, repo_root=args.repo)
        print(render_bundle_markdown(bundle))
        return
    from repoctx.protocol import op_bundle

    print(json.dumps(
        op_bundle(
            args.task,
            repo_root=args.repo,
            include_full_text=args.full,
            include_advisory=getattr(args, "include_advisory", False),
        ),
        indent=2,
    ))


bundle_cmd = SimpleNamespace(NAME="bundle", register=_register_bundle, run=_run_bundle)


# -- authority ----------------------------------------------------------------

def _register_authority(subparsers) -> None:
    a = subparsers.add_parser("authority", help="Return only authority records + constraints (v2)")
    a.add_argument("task", help="Task description")
    a.add_argument("--repo", default=".", help="Repository root")
    a.add_argument("--include", choices=("summary", "full"), default="summary")


def _run_authority(args: argparse.Namespace) -> None:
    from repoctx.protocol import op_authority

    print(json.dumps(op_authority(args.task, repo_root=args.repo, include=args.include), indent=2))


authority_cmd = SimpleNamespace(NAME="authority", register=_register_authority, run=_run_authority)


# -- scope --------------------------------------------------------------------

def _register_scope(subparsers) -> None:
    s = subparsers.add_parser("scope", help="Return edit scope for a task (v2)")
    s.add_argument("task", help="Task description")
    s.add_argument("--repo", default=".", help="Repository root")


def _run_scope(args: argparse.Namespace) -> None:
    from repoctx.protocol import op_scope

    print(json.dumps(op_scope(args.task, repo_root=args.repo), indent=2))


scope_cmd = SimpleNamespace(NAME="scope", register=_register_scope, run=_run_scope)


# -- validate-plan ------------------------------------------------------------

def _register_validate_plan(subparsers) -> None:
    v = subparsers.add_parser("validate-plan", help="Return validation plan given changed files (v2)")
    v.add_argument("task", help="Task description")
    v.add_argument("--repo", default=".", help="Repository root")
    v.add_argument("--changed", nargs="*", default=[], help="Changed file paths")


def _run_validate_plan(args: argparse.Namespace) -> None:
    from repoctx.protocol import op_validate_plan

    print(json.dumps(op_validate_plan(args.task, args.changed, repo_root=args.repo), indent=2))


validate_plan_cmd = SimpleNamespace(NAME="validate-plan", register=_register_validate_plan, run=_run_validate_plan)


# -- risk-report --------------------------------------------------------------

def _register_risk_report(subparsers) -> None:
    r = subparsers.add_parser("risk-report", help="Return risk report given changed files (v2)")
    r.add_argument("task", help="Task description")
    r.add_argument("--repo", default=".", help="Repository root")
    r.add_argument("--changed", nargs="*", default=[], help="Changed file paths")


def _run_risk_report(args: argparse.Namespace) -> None:
    from repoctx.protocol import op_risk_report

    print(json.dumps(op_risk_report(args.task, args.changed, repo_root=args.repo), indent=2))


risk_report_cmd = SimpleNamespace(NAME="risk-report", register=_register_risk_report, run=_run_risk_report)


# -- refresh ------------------------------------------------------------------

def _register_refresh(subparsers) -> None:
    rf = subparsers.add_parser("refresh", help="Return scope delta given current scope and changed files (v2)")
    rf.add_argument("task", help="Task description")
    rf.add_argument("--repo", default=".", help="Repository root")
    rf.add_argument("--changed", nargs="*", default=[], help="Changed file paths")
    rf.add_argument(
        "--current-scope-json",
        help="JSON for current edit scope (keys: allowed_paths, related_paths, protected_paths)",
    )
    rf.add_argument(
        "--no-claude-md-nudge",
        dest="claude_md_nudge",
        action="store_false",
        default=True,
        help=(
            "Skip the self-heal step that re-inserts the repoctx-nudge block "
            "into CLAUDE.md when missing. Also disabled by setting "
            "REPOCTX_NO_CLAUDE_MD_NUDGE=1."
        ),
    )


def _run_refresh(args: argparse.Namespace) -> None:
    from repoctx.protocol import op_refresh

    current_scope = None
    if args.current_scope_json:
        current_scope = json.loads(args.current_scope_json)
    print(
        json.dumps(
            op_refresh(
                args.task,
                args.changed,
                current_scope,
                repo_root=args.repo,
                claude_md_nudge=getattr(args, "claude_md_nudge", True),
            ),
            indent=2,
        )
    )


refresh_cmd = SimpleNamespace(NAME="refresh", register=_register_refresh, run=_run_refresh)


# -- detect-changes -----------------------------------------------------------

def _register_detect_changes(subparsers) -> None:
    dc = subparsers.add_parser(
        "detect-changes",
        help="Map changed files to direct + transitive callers via the import graph",
    )
    dc.add_argument("--repo", default=".", help="Repository root")
    dc.add_argument(
        "--changed",
        nargs="*",
        default=[],
        help="Changed file paths (defaults to git's dirty file list)",
    )


def _run_detect_changes(args: argparse.Namespace) -> None:
    from repoctx.protocol import op_detect_changes

    print(json.dumps(op_detect_changes(args.changed, repo_root=args.repo), indent=2))


detect_changes_cmd = SimpleNamespace(NAME="detect-changes", register=_register_detect_changes, run=_run_detect_changes)


# -- semantic-search ----------------------------------------------------------

def _register_semantic_search(subparsers) -> None:
    ss = subparsers.add_parser(
        "semantic-search",
        help="Top-K most similar indexed chunks for a query (raw embedding lookup)",
    )
    ss.add_argument("query", help="Query string to search for")
    ss.add_argument("--repo", default=".", help="Repository root")
    ss.add_argument(
        "--top",
        type=int,
        default=10,
        help="Maximum number of hits to return (default 10)",
    )
    ss.add_argument(
        "--kind",
        choices=("code", "doc", "test", "config"),
        default=None,
        help="Filter results to a single file kind",
    )


def _run_semantic_search(args: argparse.Namespace) -> None:
    from repoctx.ops import op_semantic_search

    hits = op_semantic_search(
        args.query, repo_root=args.repo, top_k=args.top, kind=args.kind,
    )
    print(json.dumps(hits, indent=2))


semantic_search_cmd = SimpleNamespace(NAME="semantic-search", register=_register_semantic_search, run=_run_semantic_search)


# -- advisory-index / advisory-search (opt-in in-flight-branch lane) -----------

def _register_advisory_index(subparsers) -> None:
    ai = subparsers.add_parser(
        "advisory-index",
        help="Build the opt-in advisory index over branches ahead of origin/main",
    )
    ai.add_argument("--repo", default=".", help="Repository root")
    ai.add_argument("--verbose", action="store_true")


def _run_advisory_index(args: argparse.Namespace) -> None:
    try:
        from repoctx.advisory import build_advisory_index
    except ImportError:
        print("Embedding dependencies not installed. Run: pip install 'repoctx-mcp[embeddings]'")
        return
    print(json.dumps(build_advisory_index(args.repo), indent=2))


advisory_index_cmd = SimpleNamespace(
    NAME="advisory-index", register=_register_advisory_index, run=_run_advisory_index,
)


def _register_advisory_search(subparsers) -> None:
    as_ = subparsers.add_parser(
        "advisory-search",
        help="Search the advisory lane (in-flight branches; NOT authoritative)",
    )
    as_.add_argument("query", help="Query string")
    as_.add_argument("--repo", default=".", help="Repository root")
    as_.add_argument("--top", type=int, default=10, help="Max hits (default 10)")


def _run_advisory_search(args: argparse.Namespace) -> None:
    from repoctx.advisory import op_advisory_search

    print(json.dumps(op_advisory_search(args.query, repo_root=args.repo, top_k=args.top), indent=2))


advisory_search_cmd = SimpleNamespace(
    NAME="advisory-search", register=_register_advisory_search, run=_run_advisory_search,
)
