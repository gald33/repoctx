"""Experiment subcommand and helpers."""

import argparse
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from repoctx.experiment import (
    build_experiment_prompt,
    collect_git_diff_stats,
    create_experiment_session,
)
from repoctx.experiment_mcp import (
    arm_control_lane_mcp_suppression,
    clear_mcp_suppression,
    control_lane_suppression_notice,
)
from repoctx.telemetry import (
    clear_active_experiment,
    load_active_experiment,
    load_experiment_session,
    record_experiment_lane,
    save_active_experiment,
)

NAME = "experiment"

EXPERIMENT_SUBCOMMANDS = {"start", "lane", "summarize"}
EXPERIMENT_HELP_USAGE = """repoctx experiment [--repo REPO]
       repoctx experiment "TASK PROMPT" [--repo REPO]
       repoctx experiment {start,lane,summarize} ..."""


def register(subparsers) -> None:
    exp = subparsers.add_parser(
        NAME,
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


def run(args: argparse.Namespace) -> None:
    if args.experiment_command == "start":
        _cmd_start(args)
        return
    if args.experiment_command == "lane" and args.lane_command == "record":
        _cmd_lane_record(args)
        return
    if args.experiment_command == "summarize":
        _cmd_summarize(args)
        return
    _cmd_resume_or_start(args)


# -- handlers -----------------------------------------------------------------


def _cmd_start(args: argparse.Namespace) -> None:
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


def _cmd_resume_or_start(args: argparse.Namespace) -> None:
    current_repo = _normalize_experiment_repo_root(Path(args.repo).resolve())
    active = load_active_experiment(repo_root=current_repo)
    if active is None:
        _cmd_wizard(args)
        return
    try:
        experiment = load_experiment_session(session_id=active["session_id"])
    except FileNotFoundError:
        clear_active_experiment(repo_root=current_repo)
        _cmd_wizard(args)
        return
    _cmd_continue(session_id=active["session_id"], experiment=experiment)


def _cmd_wizard(args: argparse.Namespace) -> None:
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


def _cmd_continue(*, session_id: str, experiment: dict[str, object]) -> None:
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
        _cmd_summarize(argparse.Namespace(session_id=session_id))
        return
    clear_active_experiment(repo_root=_experiment_repo_root(experiment["session"]))
    _cmd_summarize(argparse.Namespace(session_id=session_id))


def _cmd_lane_record(args: argparse.Namespace) -> None:
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


def _cmd_summarize(args: argparse.Namespace) -> None:
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


# -- helpers ------------------------------------------------------------------


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
