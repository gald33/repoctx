import subprocess
from pathlib import Path

from repoctx.experiment import collect_git_diff_stats, create_experiment_worktrees


def run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def commit_all(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=RepoCtx Tests",
            "-c",
            "user.email=tests@example.com",
            "commit",
            "-m",
            message,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def init_repo(repo: Path) -> str:
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True, text=True)
    write_file(repo / ".gitignore", ".worktrees/\n")
    write_file(repo / "src" / "app.py", "def run():\n    return 1\n")
    write_file(repo / "README.md", "# Demo\n")
    commit_all(repo, "initial commit")
    return run_git(repo, "rev-parse", "HEAD")


def test_create_experiment_worktrees_creates_paired_clean_checkouts(tmp_path: Path) -> None:
    base_commit = init_repo(tmp_path)

    session = create_experiment_worktrees(tmp_path, session_id="session-1")

    assert session["base_commit"] == base_commit
    assert session["control_worktree"].name == "experiment-session-1-control"
    assert session["repoctx_worktree"].name == "experiment-session-1-repoctx"
    assert (session["control_worktree"] / "src" / "app.py").exists()
    assert (session["repoctx_worktree"] / "src" / "app.py").exists()
    assert run_git(session["control_worktree"], "rev-parse", "HEAD") == base_commit
    assert run_git(session["repoctx_worktree"], "rev-parse", "HEAD") == base_commit


def test_collect_git_diff_stats_reports_isolated_counts(tmp_path: Path) -> None:
    base_commit = init_repo(tmp_path)
    session = create_experiment_worktrees(tmp_path, session_id="session-2")
    control = session["control_worktree"]

    write_file(control / "src" / "app.py", "def run():\n    return 2\n")
    write_file(control / "tests" / "test_app.py", "from src.app import run\n\ndef test_run():\n    assert run() == 2\n")
    write_file(control / "docs" / "notes.md", "# Notes\n")

    stats = collect_git_diff_stats(control, base_commit)

    assert stats["files_changed"] == 3
    assert stats["lines_added"] == 6
    assert stats["lines_deleted"] == 1
    assert stats["net_lines"] == 5
    assert stats["new_files"] == 2
    assert stats["modified_files"] == 1
    assert stats["source_files_changed"] == 1
    assert stats["test_files_changed"] == 1
    assert stats["docs_files_changed"] == 1
    assert stats["config_files_changed"] == 0
