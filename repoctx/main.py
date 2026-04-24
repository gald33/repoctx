import argparse
import json
import logging
import sys
from decimal import Decimal, InvalidOperation
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from repoctx.config import DEFAULT_EMBEDDING_CONFIG
from repoctx.experiment import (
    build_experiment_prompt,
    collect_git_diff_stats,
    create_experiment_session,
)
from repoctx.experiment_mcp import (
    arm_control_lane_mcp_suppression,
    clear_mcp_suppression,
    control_lane_suppression_notice,
    refresh_after_cli_invocation,
)
from repoctx.retriever import get_task_context
from repoctx.telemetry import (
    clear_active_experiment,
    load_active_experiment,
    load_experiment_session,
    record_experiment_lane,
    record_repoctx_invocation,
    save_active_experiment,
)

logger = logging.getLogger(__name__)

SUBCOMMANDS = {
    "query",
    "index",
    "update",
    "rebuild",
    "experiment",
    "bundle",
    "authority",
    "scope",
    "validate-plan",
    "risk-report",
    "refresh",
    "install-claude-code",
    "install-cursor",
    "install-codex",
    "init-authority",
}
EXPERIMENT_SUBCOMMANDS = {"start", "lane", "summarize"}
HELP_USAGE = """repoctx [-h] TASK
       repoctx [-h] COMMAND ..."""
EXPERIMENT_HELP_USAGE = """repoctx experiment [--repo REPO]
       repoctx experiment "TASK PROMPT" [--repo REPO]
       repoctx experiment {start,lane,summarize} ..."""
HELP_EPILOG = """Default behavior:
  If the first argument is not a subcommand, RepoCtx treats it as `query`.

Examples:
  repoctx "refactor the auth middleware to support OAuth"
  repoctx query "show me tests related to the billing webhook flow" --repo /path/to/repo --format json
  repoctx index --repo /path/to/repo
  repoctx experiment
  repoctx experiment "refactor the auth middleware to support OAuth"

Common workflows:
  Use `repoctx TASK` for the default query shorthand.
  Use `repoctx query TASK [flags]` when you need query-specific options.
  Use `repoctx experiment` to launch or resume the guided experiment flow.
  Run `repoctx COMMAND --help` for command-specific flags and examples.
"""


def _installed_version() -> str:
    try:
        return _pkg_version("repoctx-mcp")
    except PackageNotFoundError:
        return "unknown"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Local repository intelligence for coding agents",
        usage=HELP_USAGE,
        epilog=HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"repoctx {_installed_version()}",
    )
    sub = parser.add_subparsers(
        dest="command",
        title="Common subcommands",
        metavar="COMMAND",
        description="Use `repoctx COMMAND --help` for command-specific usage.",
    )

    # -- query (default when first arg isn't a subcommand) --------------------
    q = sub.add_parser("query", help="Retrieve task context (default)")
    q.add_argument("task", help="Task description to retrieve context for")
    q.add_argument("--repo", default=".", help="Repository root")
    q.add_argument("--format", choices=("markdown", "json"), default="markdown")
    q.add_argument("--verbose", action="store_true", help="Debug logging")
    q.add_argument(
        "--debug-scores", action="store_true",
        help="Print per-file score breakdown (heuristic / embedding / final)",
    )
    q.add_argument("--no-embeddings", action="store_true", help="Disable embedding retrieval")
    q.add_argument("--session-id", help="Telemetry session ID")
    q.add_argument("--task-id", help="Telemetry task ID")
    q.add_argument(
        "--variant", choices=("control", "repoctx"), default="repoctx",
        help="Experiment variant label",
    )

    # -- index ----------------------------------------------------------------
    idx = sub.add_parser("index", help="Build the embedding index for a repo")
    idx.add_argument("--repo", default=".", help="Repository root")
    idx.add_argument("--verbose", action="store_true")

    # -- update ---------------------------------------------------------------
    upd = sub.add_parser("update", help="Re-embed a single file")
    upd.add_argument("file", help="Relative path of the file to update")
    upd.add_argument("--repo", default=".", help="Repository root")
    upd.add_argument("--verbose", action="store_true")

    # -- rebuild --------------------------------------------------------------
    rb = sub.add_parser("rebuild", help="Delete and rebuild the embedding index")
    rb.add_argument("--repo", default=".", help="Repository root")
    rb.add_argument("--verbose", action="store_true")

    # -- experiment -----------------------------------------------------------
    exp = sub.add_parser(
        "experiment",
        help="Run a controlled control-vs-repoctx experiment",
        description="Launch or resume the guided experiment flow.",
        usage=EXPERIMENT_HELP_USAGE,
        epilog=(
            "Examples:\n"
            "  repoctx experiment\n"
            "  repoctx experiment --repo /path/to/repo\n"
            "  repoctx experiment \"refactor the auth middleware to support OAuth\"\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    exp.add_argument("--repo", default=".", help="Repository root for wizard mode")
    exp_sub = exp.add_subparsers(dest="experiment_command")

    exp_start = exp_sub.add_parser("start", help="Create experiment session and paired worktrees")
    exp_start.add_argument("task", help="Task prompt to use for both lanes")
    exp_start.add_argument("--repo", default=argparse.SUPPRESS, help="Repository root")

    exp_lane = exp_sub.add_parser("lane", help="Record one experiment lane")
    lane_sub = exp_lane.add_subparsers(dest="lane_command")
    lane_record = lane_sub.add_parser("record", help="Record lane cost checkpoints and git stats")
    lane_record.add_argument("--session-id", required=True, help="Experiment session ID")
    lane_record.add_argument("--lane", choices=("control", "repoctx"), required=True)
    lane_record.add_argument("--before", help="Total cost before this lane started")
    lane_record.add_argument("--after", help="Total cost after this lane finished")
    lane_record.add_argument("--completion-status", help="Optional completion status")
    lane_record.add_argument("--verification-status", help="Optional verification status")
    lane_record.add_argument("--outcome-summary", help="Optional short outcome summary")
    lane_record.add_argument("--notes", help="Optional notes")
    lane_record.add_argument("--overwrite", action="store_true", help="Overwrite an existing lane record")

    exp_summary = exp_sub.add_parser("summarize", help="Summarize an experiment session")
    exp_summary.add_argument("--session-id", required=True, help="Experiment session ID")

    # -- repoctx v2 protocol ops ---------------------------------------------
    b = sub.add_parser("bundle", help="Build the ground-truth bundle for a task (v2)")
    b.add_argument("task", help="Task description")
    b.add_argument("--repo", default=".", help="Repository root")
    b.add_argument("--full", action="store_true", help="Include full authority text")
    b.add_argument("--format", choices=("json", "markdown"), default="json")

    a = sub.add_parser("authority", help="Return only authority records + constraints (v2)")
    a.add_argument("task", help="Task description")
    a.add_argument("--repo", default=".", help="Repository root")
    a.add_argument("--include", choices=("summary", "full"), default="summary")

    s = sub.add_parser("scope", help="Return edit scope for a task (v2)")
    s.add_argument("task", help="Task description")
    s.add_argument("--repo", default=".", help="Repository root")

    v = sub.add_parser("validate-plan", help="Return validation plan given changed files (v2)")
    v.add_argument("task", help="Task description")
    v.add_argument("--repo", default=".", help="Repository root")
    v.add_argument("--changed", nargs="*", default=[], help="Changed file paths")

    r = sub.add_parser("risk-report", help="Return risk report given changed files (v2)")
    r.add_argument("task", help="Task description")
    r.add_argument("--repo", default=".", help="Repository root")
    r.add_argument("--changed", nargs="*", default=[], help="Changed file paths")

    rf = sub.add_parser("refresh", help="Return scope delta given current scope and changed files (v2)")
    rf.add_argument("task", help="Task description")
    rf.add_argument("--repo", default=".", help="Repository root")
    rf.add_argument("--changed", nargs="*", default=[], help="Changed file paths")
    rf.add_argument(
        "--current-scope-json",
        help="JSON for current edit scope (keys: allowed_paths, related_paths, protected_paths)",
    )

    ic = sub.add_parser(
        "install-claude-code",
        help="Install AGENTS.md section + .mcp.json entry for repoctx (v2)",
    )
    ic.add_argument("--repo", default=".", help="Repository root")

    icu = sub.add_parser(
        "install-cursor",
        help="Install AGENTS.md section + .cursor/mcp.json entry for repoctx (v2)",
    )
    icu.add_argument("--repo", default=".", help="Repository root")

    ico = sub.add_parser(
        "install-codex",
        help="Install AGENTS.md section + .codex/mcp.json entry for repoctx (v2)",
    )
    ico.add_argument("--repo", default=".", help="Repository root")

    ia = sub.add_parser(
        "init-authority",
        help="Scaffold contracts/ + docs/architecture/ + examples/ starter layout (v2)",
    )
    ia.add_argument("--repo", default=".", help="Repository root")

    return parser


def main() -> None:
    _ensure_default_subcommand()
    parser = build_parser()
    args = parser.parse_args()
    refresh_after_cli_invocation()
    logging.basicConfig(level=logging.DEBUG if getattr(args, "verbose", False) else logging.WARNING)

    cmd = args.command or "query"
    if cmd == "query":
        _cmd_query(args)
    elif cmd == "index":
        _cmd_index(args)
    elif cmd == "update":
        _cmd_update(args)
    elif cmd == "rebuild":
        _cmd_rebuild(args)
    elif cmd == "experiment":
        _cmd_experiment(args)
    elif cmd == "bundle":
        _cmd_bundle(args)
    elif cmd == "authority":
        _cmd_authority(args)
    elif cmd == "scope":
        _cmd_scope(args)
    elif cmd == "validate-plan":
        _cmd_validate_plan(args)
    elif cmd == "risk-report":
        _cmd_risk_report(args)
    elif cmd == "refresh":
        _cmd_refresh(args)
    elif cmd == "install-claude-code":
        _cmd_install_claude_code(args)
    elif cmd == "install-cursor":
        _cmd_install_cursor(args)
    elif cmd == "install-codex":
        _cmd_install_codex(args)
    elif cmd == "init-authority":
        _cmd_init_authority(args)
    else:
        parser.print_help()
        raise SystemExit(1)


def _cmd_bundle(args: argparse.Namespace) -> None:
    if getattr(args, "format", "json") == "markdown":
        from repoctx.bundle import build_bundle, render_bundle_markdown

        bundle = build_bundle(args.task, repo_root=args.repo)
        print(render_bundle_markdown(bundle))
        return
    from repoctx.protocol import op_bundle

    print(json.dumps(op_bundle(args.task, repo_root=args.repo, include_full_text=args.full), indent=2))


def _cmd_authority(args: argparse.Namespace) -> None:
    from repoctx.protocol import op_authority

    print(json.dumps(op_authority(args.task, repo_root=args.repo, include=args.include), indent=2))


def _cmd_scope(args: argparse.Namespace) -> None:
    from repoctx.protocol import op_scope

    print(json.dumps(op_scope(args.task, repo_root=args.repo), indent=2))


def _cmd_validate_plan(args: argparse.Namespace) -> None:
    from repoctx.protocol import op_validate_plan

    print(json.dumps(op_validate_plan(args.task, args.changed, repo_root=args.repo), indent=2))


def _cmd_risk_report(args: argparse.Namespace) -> None:
    from repoctx.protocol import op_risk_report

    print(json.dumps(op_risk_report(args.task, args.changed, repo_root=args.repo), indent=2))


def _cmd_refresh(args: argparse.Namespace) -> None:
    from repoctx.protocol import op_refresh

    current_scope = None
    if args.current_scope_json:
        current_scope = json.loads(args.current_scope_json)
    print(
        json.dumps(
            op_refresh(args.task, args.changed, current_scope, repo_root=args.repo),
            indent=2,
        )
    )


def _cmd_install_claude_code(args: argparse.Namespace) -> None:
    from repoctx.harness import install_claude_code

    result = install_claude_code(repo_root=args.repo)
    print(json.dumps(result.to_dict(), indent=2))


def _cmd_install_cursor(args: argparse.Namespace) -> None:
    from repoctx.harness import install_cursor

    result = install_cursor(repo_root=args.repo)
    print(json.dumps(result.to_dict(), indent=2))


def _cmd_install_codex(args: argparse.Namespace) -> None:
    from repoctx.harness import install_codex

    result = install_codex(repo_root=args.repo)
    print(json.dumps(result.to_dict(), indent=2))


def _cmd_init_authority(args: argparse.Namespace) -> None:
    from repoctx.authority.scaffold import init_authority

    result = init_authority(repo_root=args.repo)
    print(json.dumps(result.to_dict(), indent=2))


def _ensure_default_subcommand() -> None:
    """If the first non-flag arg is not a known subcommand, insert 'query'."""
    if len(sys.argv) < 2:
        return
    first = sys.argv[1]
    if first == "experiment":
        idx = 2
        while idx < len(sys.argv):
            token = sys.argv[idx]
            if token in EXPERIMENT_SUBCOMMANDS or token in {"-h", "--help"}:
                return
            if token == "--repo":
                idx += 2
                continue
            if token.startswith("-"):
                idx += 1
                continue
            sys.argv.insert(idx, "start")
            return
        return
    if first not in SUBCOMMANDS and not first.startswith("-"):
        sys.argv.insert(1, "query")


# -- subcommand handlers ------------------------------------------------------


def _cmd_query(args: argparse.Namespace) -> None:
    started = perf_counter()
    session_id = getattr(args, "session_id", None) or uuid4().hex
    task_id = getattr(args, "task_id", None) or uuid4().hex
    debug = getattr(args, "debug_scores", False)
    repo = Path(args.repo)

    embedding_scores = _load_embedding_scores(args.task, repo, args)

    try:
        response = get_task_context(
            task=args.task,
            repo_root=repo,
            embedding_scores=embedding_scores,
        )
    except Exception as exc:
        _record_telemetry(
            task=args.task, repo_root=repo, session_id=session_id,
            task_id=task_id, variant=args.variant, response=None,
            output_format=args.format, success=False,
            error_type=type(exc).__name__,
            duration_ms=int((perf_counter() - started) * 1000),
        )
        logger.error("repoctx failed: %s", exc)
        raise SystemExit(1) from exc

    _record_telemetry(
        task=args.task, repo_root=repo, session_id=session_id,
        task_id=task_id, variant=args.variant, response=response,
        output_format=args.format, success=True, error_type=None,
        duration_ms=int((perf_counter() - started) * 1000),
    )

    if debug:
        _print_debug_scores(response)

    if args.format == "json":
        print(json.dumps(response.to_dict(include_debug=debug), indent=2))
        return
    print(response.context_markdown)


def _cmd_index(args: argparse.Namespace) -> None:
    _build_and_save_index(Path(args.repo))


def _cmd_rebuild(args: argparse.Namespace) -> None:
    import shutil

    repo = Path(args.repo).resolve()
    emb_dir = repo / DEFAULT_EMBEDDING_CONFIG.index_dir / "embeddings"
    if emb_dir.exists():
        shutil.rmtree(emb_dir)
        logger.info("Removed existing index at %s", emb_dir)
    _build_and_save_index(repo)


def _cmd_update(args: argparse.Namespace) -> None:
    try:
        from repoctx.embeddings import update_file_in_index
    except ImportError:
        print("Embedding dependencies not installed. Run: pip install 'repoctx-mcp[embeddings]'", file=sys.stderr)
        raise SystemExit(1)
    try:
        update_file_in_index(args.file, repo_root=Path(args.repo))
    except (ImportError, FileNotFoundError) as exc:
        print(f"{exc}", file=sys.stderr)
        raise SystemExit(1)
    print(f"Updated embedding for {args.file}")


def _cmd_experiment(args: argparse.Namespace) -> None:
    if args.experiment_command == "start":
        _cmd_experiment_start(args)
        return
    if args.experiment_command == "lane" and args.lane_command == "record":
        _cmd_experiment_lane_record(args)
        return
    if args.experiment_command == "summarize":
        _cmd_experiment_summarize(args)
        return
    _cmd_experiment_resume_or_start(args)


# -- helpers -------------------------------------------------------------------


def _build_and_save_index(repo: Path) -> None:
    try:
        from repoctx.embeddings import build_index
    except ImportError:
        print("Embedding dependencies not installed. Run: pip install 'repoctx-mcp[embeddings]'", file=sys.stderr)
        raise SystemExit(1)

    repo = repo.resolve()
    try:
        record_store = build_index(repo)
    except ImportError as exc:
        print(f"{exc}", file=sys.stderr)
        raise SystemExit(1)
    emb_dir = repo / DEFAULT_EMBEDDING_CONFIG.index_dir / "embeddings"
    record_store.save(emb_dir)
    print(f"Indexed {len(record_store)} files → {emb_dir}")


def _cmd_experiment_start(args: argparse.Namespace) -> None:
    repo = _normalize_experiment_repo_root(Path(args.repo).resolve())
    session_id = uuid4().hex
    task_id = uuid4().hex
    session = create_experiment_session(
        repo,
        session_id=session_id,
        task_id=task_id,
        task_prompt=args.task,
        query=args.task,
    )
    save_active_experiment(session_id=session_id, repo_root=repo)
    _print_experiment_created(session_id=session_id, session=session)


def _cmd_experiment_resume_or_start(args: argparse.Namespace) -> None:
    current_repo = _normalize_experiment_repo_root(Path(args.repo).resolve())
    active = load_active_experiment(repo_root=current_repo)
    if active is None:
        _cmd_experiment_wizard(args)
        return
    try:
        experiment = load_experiment_session(session_id=active["session_id"])
    except FileNotFoundError:
        clear_active_experiment(repo_root=current_repo)
        _cmd_experiment_wizard(args)
        return
    _cmd_experiment_continue(session_id=active["session_id"], experiment=experiment)


def _cmd_experiment_wizard(args: argparse.Namespace) -> None:
    repo = _normalize_experiment_repo_root(Path(args.repo).resolve())
    print("Experiment setup wizard")
    print("Press Enter to skip optional fields. Finish the prompt with a blank line.")
    print("")

    label = input("Optional label: ").strip() or None
    guardrail_mode = _prompt_guardrail_mode()
    prompt = _prompt_multiline_task()
    final_prompt = build_experiment_prompt(prompt, guardrail_mode=guardrail_mode)
    print("")
    print("Confirm experiment setup")
    print(f"Repo: {repo}")
    print(f"Label: {label or '(none)'}")
    print(f"Guardrails: {guardrail_mode}")
    print("Prompt preview:")
    print(final_prompt)
    print("")
    if not _prompt_yes_no("Create experiment with this setup? [y/N]: ", default=False):
        print("Experiment setup cancelled.")
        raise SystemExit(1)

    session_id = uuid4().hex
    task_id = uuid4().hex
    session = create_experiment_session(
        repo,
        session_id=session_id,
        task_id=task_id,
        task_prompt=prompt,
        query=prompt,
        label=label,
        guardrail_mode=guardrail_mode,
    )
    save_active_experiment(session_id=session_id, repo_root=repo)
    _print_experiment_created(session_id=session_id, session=session)


def _cmd_experiment_continue(*, session_id: str, experiment: dict[str, object]) -> None:
    lanes = experiment["lanes"]
    if "control" not in lanes:
        _print_lane_recording_reminder(experiment["session"], lane="control")
        _prompt_and_record_lane(session_id=session_id, experiment=experiment, lane="control")
        refreshed = load_experiment_session(session_id=session_id)
        _print_lane_handoff(refreshed["session"], lane="repoctx")
        return
    if "repoctx" not in lanes:
        _print_lane_recording_reminder(experiment["session"], lane="repoctx")
        _prompt_and_record_lane(session_id=session_id, experiment=experiment, lane="repoctx")
        clear_active_experiment(repo_root=_experiment_repo_root(experiment["session"]))
        _cmd_experiment_summarize(argparse.Namespace(session_id=session_id))
        return
    clear_active_experiment(repo_root=_experiment_repo_root(experiment["session"]))
    _cmd_experiment_summarize(argparse.Namespace(session_id=session_id))


def _cmd_experiment_lane_record(args: argparse.Namespace) -> None:
    experiment = load_experiment_session(session_id=args.session_id)
    _record_lane_result(
        session_id=args.session_id,
        experiment=experiment,
        lane=args.lane,
        before_raw=args.before,
        after_raw=args.after,
        completion_status=args.completion_status,
        verification_status=args.verification_status,
        outcome_summary=args.outcome_summary,
        notes=args.notes,
        overwrite=args.overwrite,
    )


def _cmd_experiment_summarize(args: argparse.Namespace) -> None:
    experiment = load_experiment_session(session_id=args.session_id)
    session = experiment["session"]
    lanes = experiment["lanes"]
    missing = [lane for lane in ("control", "repoctx") if lane not in lanes]

    print("Experiment summary")
    print(f"Task: {session['prompt']}")
    print(f"Session: {session['session_id']}")
    print(f"Base commit: {session['base_commit']}")
    print(f"Prompt hash: {session['prompt_hash']}")
    print("")

    if missing:
        print(f"Missing lane results: {', '.join(_lane_display_name(lane) for lane in missing)}")
        for lane in missing:
            print(f"repoctx experiment lane record --session-id {args.session_id} --lane {lane}")
        return

    control = lanes["control"]
    repoctx = lanes["repoctx"]
    control_delta = Decimal(control["cost_delta_usd"])
    repoctx_delta = Decimal(repoctx["cost_delta_usd"])
    savings = control_delta - repoctx_delta
    print(_format_lane_summary("control", control))
    print("")
    print(_format_lane_summary("treatment", repoctx))
    print("")
    print("difference")
    print(f"treatment saved: {_format_money(savings)}")
    if control_delta > 0:
        print(f"treatment saved: {(savings / control_delta) * Decimal('100'):.1f}%")
    winner = "treatment" if savings > 0 else "control"
    print(f"winner: {winner}")


def _record_lane_result(
    *,
    session_id: str,
    experiment: dict[str, object],
    lane: str,
    before_raw: str | None,
    after_raw: str | None,
    completion_status: str | None,
    verification_status: str | None,
    outcome_summary: str | None,
    notes: str | None,
    overwrite: bool = False,
) -> None:
    if lane in experiment["lanes"] and not overwrite:
        print(
            f"Lane '{lane}' already recorded for session {session_id}. "
            "Pass --overwrite to record it again.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    display_name = _lane_display_name(lane)
    before = _parse_cost_or_prompt(
        raw_value=before_raw,
        prompt_text=f"Enter the total cost before starting the {display_name} lane (USD): ",
    )
    after = _parse_cost_or_prompt(
        raw_value=after_raw,
        prompt_text=f"Enter the total cost after finishing the {display_name} lane (USD): ",
    )
    if before < 0 or after < 0:
        print("Costs must be non-negative.", file=sys.stderr)
        raise SystemExit(1)
    if after < before:
        print("The after cost must be greater than or equal to the before cost.", file=sys.stderr)
        raise SystemExit(1)

    session_data = experiment["session"]
    worktree_path = Path(session_data[f"{lane}_worktree"])
    stats = collect_git_diff_stats(worktree_path, session_data["base_commit"])
    record_experiment_lane(
        session_id=session_id,
        task_id=session_data["task_id"],
        lane=lane,
        worktree_path=worktree_path,
        cost_before_usd=before,
        cost_after_usd=after,
        completion_status=completion_status,
        verification_status=verification_status,
        outcome_summary=outcome_summary,
        notes=notes,
        stats=stats,
    )
    clear_mcp_suppression()
    print(
        f"Recorded {display_name} lane: "
        f"delta {_format_money(after - before)}, "
        f"{stats['files_changed']} files changed."
    )


def _prompt_and_record_lane(*, session_id: str, experiment: dict[str, object], lane: str) -> None:
    before_raw = input(f"Enter the total cost before starting the {_lane_display_name(lane)} lane (USD): ")
    after_raw = input(f"Enter the total cost after finishing the {_lane_display_name(lane)} lane (USD): ")
    completion_status = _prompt_optional(f"Completion status for the {_lane_display_name(lane)} lane: ")
    verification_status = _prompt_optional(f"Verification status for the {_lane_display_name(lane)} lane: ")
    outcome_summary = _prompt_optional("Outcome summary (optional): ")
    notes = _prompt_optional("Notes (optional): ")
    _record_lane_result(
        session_id=session_id,
        experiment=experiment,
        lane=lane,
        before_raw=before_raw,
        after_raw=after_raw,
        completion_status=completion_status,
        verification_status=verification_status,
        outcome_summary=outcome_summary,
        notes=notes,
    )


def _print_experiment_created(*, session_id: str, session: dict[str, object]) -> None:
    print("Experiment created")
    print(f"Task: {session['prompt']}")
    print(f"Session: {session_id}")
    print(f"Base commit: {session['base_commit']}")
    if session.get("label"):
        print(f"Label: {session['label']}")
    if session.get("guardrail_mode"):
        print(f"Guardrails: {session['guardrail_mode']}")
    print("")
    _print_lane_handoff(session, lane="control")


def _print_lane_handoff(session: dict[str, object], *, lane: str) -> None:
    step_title = "Step 1 of 3: Run control lane" if lane == "control" else "Step 2 of 3: Run treatment lane"
    worktree_key = "control_worktree" if lane == "control" else "repoctx_worktree"
    worktree_path = Path(str(session[worktree_key]))
    print(step_title)
    print(f"Worktree: {worktree_path}")
    print("")
    print("Use this exact prompt in the agent:")
    print(session["prompt"])
    print("")
    if lane == "control":
        armed = arm_control_lane_mcp_suppression()
        notice = control_lane_suppression_notice(armed=armed)
        if notice:
            print(notice)
            print("")
        warning = _control_lane_mcp_warning(worktree_path, mcp_suppress_armed=armed)
        if warning:
            print(warning)
            print("")
        print("Treatment lane will enable RepoCtx MCP in that worktree.")
    else:
        clear_mcp_suppression()
        config_path = _write_treatment_cursor_config(worktree_path)
        print(f"RepoCtx MCP enabled in: {config_path}")
        print("Restart Cursor after opening this worktree if it does not pick up the new MCP config.")
    print("Run the agent now, then rerun `repoctx experiment`.")


def _print_lane_recording_reminder(session: dict[str, object], *, lane: str) -> None:
    worktree_key = "control_worktree" if lane == "control" else "repoctx_worktree"
    worktree_path = Path(str(session[worktree_key]))
    print(f"Resume {_lane_display_name(lane)} lane")
    print(f"Worktree: {worktree_path}")
    print("Use this exact prompt in the agent:")
    print(session["prompt"])
    if lane == "control":
        armed = arm_control_lane_mcp_suppression()
        notice = control_lane_suppression_notice(armed=armed)
        if notice:
            print(notice)
        warning = _control_lane_mcp_warning(worktree_path, mcp_suppress_armed=armed)
        if warning:
            print(warning)
    else:
        clear_mcp_suppression()
        config_path = _write_treatment_cursor_config(worktree_path)
        print(f"RepoCtx MCP enabled in: {config_path}")
    print(f"If you already finished the {_lane_display_name(lane)} lane, record it below.")


def _prompt_guardrail_mode() -> str:
    enabled = _prompt_yes_no("Add strict comparison guardrails to the prompt? [Y/n]: ", default=True)
    return "strict" if enabled else "none"


def _prompt_multiline_task() -> str:
    print("Enter the shared experiment prompt:")
    lines: list[str] = []
    while True:
        line = input("> ")
        if not line.strip():
            if lines:
                break
            print("Prompt cannot be empty. Enter at least one line.")
            continue
        lines.append(line)
    return "\n".join(lines)


def _prompt_yes_no(prompt_text: str, *, default: bool) -> bool:
    while True:
        raw_value = input(prompt_text).strip().lower()
        if not raw_value:
            return default
        if raw_value in {"y", "yes"}:
            return True
        if raw_value in {"n", "no"}:
            return False
        print("Please answer yes or no.")


def _prompt_optional(prompt_text: str) -> str | None:
    value = input(prompt_text).strip()
    return value or None


def _lane_display_name(lane: str) -> str:
    return "treatment" if lane == "repoctx" else lane


def _experiment_repo_root(session_payload: dict[str, object]) -> Path:
    return Path(str(session_payload["control_worktree"])).resolve().parents[1]


def _normalize_experiment_repo_root(repo: Path) -> Path:
    resolved = repo.resolve()
    for candidate in (resolved, *resolved.parents):
        if candidate.name == ".worktrees":
            return candidate.parent
    return resolved


def _control_lane_mcp_warning(worktree_path: Path, *, mcp_suppress_armed: bool) -> str | None:
    global_config = Path.home() / ".cursor" / "mcp.json"
    project_config = worktree_path / ".cursor" / "mcp.json"
    if mcp_suppress_armed and _cursor_config_has_repoctx(global_config):
        return None
    if _cursor_config_has_repoctx(global_config):
        return (
            "Warning: ~/.cursor/mcp.json already enables RepoCtx, so the control lane may still "
            "see RepoCtx tools unless you use a clean Cursor profile."
        )
    if _cursor_config_has_repoctx(project_config):
        return (
            "Warning: this worktree already has .cursor/mcp.json with RepoCtx enabled, so the control "
            "lane is not isolated until you remove or disable that project-level config."
        )
    return None


def _write_treatment_cursor_config(worktree_path: Path) -> Path:
    config_path = worktree_path / ".cursor" / "mcp.json"
    payload: dict[str, object] = {}
    if config_path.exists():
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
    servers = payload.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        payload["mcpServers"] = {}
        servers = payload["mcpServers"]
    servers["repoctx"] = {
        "command": "python3",
        "args": ["-m", "repoctx.mcp_server"],
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return config_path


def _cursor_config_has_repoctx(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    servers = payload.get("mcpServers")
    return isinstance(servers, dict) and "repoctx" in servers


def _load_embedding_scores(
    task: str,
    repo_root: Path,
    args: argparse.Namespace,
) -> dict[str, float] | None:
    if getattr(args, "no_embeddings", False):
        return None
    try:
        from repoctx.embeddings import try_load_retriever

        retriever = try_load_retriever(repo_root)
        if retriever is None:
            return None
        return retriever.query_scores(task)
    except Exception:
        return None


def _parse_cost_or_prompt(*, raw_value: str | None, prompt_text: str) -> Decimal:
    if raw_value is None:
        raw_value = input(prompt_text)
    try:
        return Decimal(raw_value)
    except (InvalidOperation, TypeError):
        print(f"Invalid cost value: {raw_value}", file=sys.stderr)
        raise SystemExit(1)


def _format_money(value: Decimal) -> str:
    return f"${value.quantize(Decimal('0.01'))}"


def _format_lane_summary(label: str, payload: dict[str, object]) -> str:
    stats = payload.get("stats", {})
    lines = [
        label,
        f"before: {_format_money(Decimal(str(payload['cost_before_usd'])))}",
        f"after:  {_format_money(Decimal(str(payload['cost_after_usd'])))}",
        f"delta:  {_format_money(Decimal(str(payload['cost_delta_usd'])))}",
        f"files changed: {stats.get('files_changed', 0)}",
        f"lines added/deleted: {stats.get('lines_added', 0)}/{stats.get('lines_deleted', 0)}",
        f"completion: {payload.get('completion_status') or 'n/a'}",
        f"verification: {payload.get('verification_status') or 'n/a'}",
    ]
    return "\n".join(lines)


def _print_debug_scores(response) -> None:
    """Write a compact score breakdown table to stderr."""
    import sys

    sections = [
        ("docs", response.relevant_docs),
        ("files", response.relevant_files),
        ("tests", response.related_tests),
        ("neighbors", response.graph_neighbors),
    ]
    print("\n--- Score breakdown ---", file=sys.stderr)
    for label, items in sections:
        if not items:
            continue
        print(f"\n  [{label}]", file=sys.stderr)
        for item in items:
            emb_part = f"  emb={item.embedding_score:.3f}" if item.embedding_score else ""
            print(
                f"    {item.path:60s}  heur={item.heuristic_score:6.1f}{emb_part}  final={item.score:6.1f}",
                file=sys.stderr,
            )
    print("", file=sys.stderr)


def _record_telemetry(
    *,
    task: str,
    repo_root: Path,
    session_id: str,
    task_id: str,
    variant: str,
    response,
    output_format: str,
    success: bool,
    error_type: str | None,
    duration_ms: int,
) -> None:
    metrics = response.metrics if response is not None else None
    output_bytes = 0
    if response is not None:
        output_text = (
            json.dumps(response.to_dict(), indent=2)
            if output_format == "json"
            else response.context_markdown
        )
        output_bytes = len(output_text.encode("utf-8"))

    try:
        record_repoctx_invocation(
            session_id=session_id,
            task_id=task_id,
            variant=variant,
            surface="cli",
            query=task,
            repo_root=repo_root,
            success=success,
            error_type=error_type,
            repoctx_duration_ms=duration_ms,
            scan_duration_ms=metrics.scan_duration_ms if metrics is not None else 0,
            files_considered=metrics.files_considered if metrics is not None else 0,
            files_selected=metrics.files_selected if metrics is not None else 0,
            docs_selected=metrics.docs_selected if metrics is not None else 0,
            tests_selected=metrics.tests_selected if metrics is not None else 0,
            neighbors_selected=metrics.neighbors_selected if metrics is not None else 0,
            output_format=output_format,
            output_bytes=output_bytes,
        )
    except Exception:
        if logger.isEnabledFor(logging.DEBUG):
            logger.warning("Failed to record telemetry", exc_info=True)


if __name__ == "__main__":
    main()
