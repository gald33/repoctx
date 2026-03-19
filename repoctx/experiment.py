from __future__ import annotations

import subprocess
from pathlib import Path

from repoctx.config import DEFAULT_CONFIG
from repoctx.telemetry import record_experiment_session

STRICT_GUARDRAILS_TEXT = """Strict comparison guardrails:
- Do not broaden scope.
- Do not make adjacent improvements.
- Do not refactor unrelated code.
- Modify only files required by the requested change.
- Stop as soon as the definition of done is satisfied."""


def create_experiment_worktrees(repo_root: str | Path, *, session_id: str) -> dict[str, Path | str]:
    repo = Path(repo_root).resolve()
    worktrees_dir = repo / ".worktrees"
    _verify_worktrees_ignored(repo)
    worktrees_dir.mkdir(parents=True, exist_ok=True)

    base_commit = _git_stdout(repo, "rev-parse", "HEAD")
    control_name = f"experiment-{session_id}-control"
    repoctx_name = f"experiment-{session_id}-repoctx"
    control_path = worktrees_dir / control_name
    repoctx_path = worktrees_dir / repoctx_name

    _git(repo, "worktree", "add", str(control_path), "-b", control_name, base_commit)
    _git(repo, "worktree", "add", str(repoctx_path), "-b", repoctx_name, base_commit)

    return {
        "base_commit": base_commit,
        "control_worktree": control_path,
        "repoctx_worktree": repoctx_path,
    }


def normalize_experiment_prompt(prompt: str) -> str:
    lines = [line.rstrip() for line in prompt.strip().splitlines()]
    return "\n".join(lines).strip()


def build_experiment_prompt(task_prompt: str, *, guardrail_mode: str = "none") -> str:
    normalized = normalize_experiment_prompt(task_prompt)
    if guardrail_mode != "strict":
        return normalized
    return f"{normalized}\n\n{STRICT_GUARDRAILS_TEXT}"


def create_experiment_session(
    repo_root: str | Path,
    *,
    session_id: str,
    task_id: str,
    task_prompt: str,
    query: str | None = None,
    label: str | None = None,
    guardrail_mode: str = "none",
) -> dict[str, Path | str]:
    repo = Path(repo_root).resolve()
    prompt = build_experiment_prompt(task_prompt, guardrail_mode=guardrail_mode)
    session = create_experiment_worktrees(repo, session_id=session_id)
    record_experiment_session(
        session_id=session_id,
        task_id=task_id,
        query=query or task_prompt,
        repo_root=repo,
        prompt=prompt,
        base_commit=session["base_commit"],
        control_worktree=session["control_worktree"],
        repoctx_worktree=session["repoctx_worktree"],
        label=label,
        guardrail_mode=guardrail_mode,
    )
    return {
        **session,
        "prompt": prompt,
        "label": label,
        "guardrail_mode": guardrail_mode,
    }


def collect_git_diff_stats(worktree_path: str | Path, base_commit: str) -> dict[str, int]:
    worktree = Path(worktree_path).resolve()
    changed_paths: list[tuple[str, str]] = []
    for line in _git_stdout(
        worktree,
        "status",
        "--porcelain",
        "--untracked-files=all",
    ).splitlines():
        if not line.strip():
            continue
        status = line[:2]
        raw_path = line[3:]
        path = raw_path.split(" -> ", 1)[-1]
        changed_paths.append((status, path))

    stats = {
        "files_changed": len(changed_paths),
        "lines_added": 0,
        "lines_deleted": 0,
        "net_lines": 0,
        "new_files": 0,
        "modified_files": 0,
        "source_files_changed": 0,
        "test_files_changed": 0,
        "docs_files_changed": 0,
        "config_files_changed": 0,
    }
    for status, path_str in changed_paths:
        path = Path(path_str)
        if status == "??":
            stats["new_files"] += 1
            stats["lines_added"] += _count_file_lines(worktree / path)
        else:
            stats["modified_files"] += 1
            added, deleted = _diff_numstat_for_path(worktree, base_commit, path_str)
            stats["lines_added"] += added
            stats["lines_deleted"] += deleted

        if _is_test_path(path):
            stats["test_files_changed"] += 1
        elif path.suffix.lower() in {".md", ".mdc"}:
            stats["docs_files_changed"] += 1
        elif path.suffix.lower() in DEFAULT_CONFIG.config_extensions:
            stats["config_files_changed"] += 1
        elif path.suffix.lower() in DEFAULT_CONFIG.code_extensions:
            stats["source_files_changed"] += 1
    stats["net_lines"] = stats["lines_added"] - stats["lines_deleted"]
    return stats


def _verify_worktrees_ignored(repo: Path) -> None:
    result = subprocess.run(
        ["git", "check-ignore", ".worktrees/__repoctx_probe__"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError("`.worktrees` must be ignored before creating experiment worktrees.")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _git_stdout(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.rstrip("\n")


def _is_test_path(path: Path) -> bool:
    lower_name = path.name.lower()
    lower_path = "/".join(part.lower() for part in path.parts)
    if "tests/" in f"{lower_path}/" or lower_path.startswith("tests/"):
        return True
    return any(marker in lower_name for marker in DEFAULT_CONFIG.test_markers)


def _count_file_lines(path: Path) -> int:
    try:
        return len(path.read_text(encoding="utf-8").splitlines())
    except UnicodeDecodeError:
        return 0


def _diff_numstat_for_path(worktree: Path, base_commit: str, path: str) -> tuple[int, int]:
    output = _git_stdout(
        worktree,
        "diff",
        "--numstat",
        "--find-renames",
        base_commit,
        "--",
        path,
    )
    if not output:
        return (0, 0)
    added, deleted, _ = output.split("\t", 2)
    return (
        0 if added == "-" else int(added),
        0 if deleted == "-" else int(deleted),
    )
