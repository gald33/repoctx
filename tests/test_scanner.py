from pathlib import Path

from repoctx.scanner import scan_repository


def write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_scan_repository_detects_high_value_docs(tmp_path: Path) -> None:
    write_file(tmp_path / "AGENTS.md", "# Root agent guide\n")
    write_file(tmp_path / "README.md", "# Root readme\n")
    write_file(tmp_path / ".cursor" / "rules" / "workspace.mdc", "Use typed APIs.\n")
    write_file(tmp_path / "packages" / "billing" / "README.md", "# Billing package\n")
    write_file(tmp_path / "src" / "billing" / "retry.ts", "export const retry = true;\n")
    write_file(tmp_path / "node_modules" / "ignored.ts", "export const ignored = true;\n")
    write_file(
        tmp_path / ".uv-cache" / "archive-v0" / "pkg" / "metadata.py",
        "# build cache\n",
    )

    index = scan_repository(tmp_path)

    doc_paths = [record.path for record in index.docs]

    assert "AGENTS.md" in doc_paths
    assert "README.md" in doc_paths
    assert ".cursor/rules/workspace.mdc" in doc_paths
    assert "packages/billing/README.md" in doc_paths
    assert "node_modules/ignored.ts" not in index.records
    assert all(not p.startswith(".uv-cache/") for p in index.records)

    ranked_docs = {record.path: record.doc_score for record in index.docs}
    assert ranked_docs["AGENTS.md"] > ranked_docs["packages/billing/README.md"]
    assert ranked_docs["README.md"] > 0


def test_scan_repository_ignores_worktrees_directory(tmp_path: Path) -> None:
    write_file(tmp_path / ".worktrees" / "feature-a" / "README.md", "# Nested worktree readme\n")
    write_file(tmp_path / "src" / "main.py", "def run():\n    return True\n")

    index = scan_repository(tmp_path)

    assert ".worktrees/feature-a/README.md" not in index.records
    assert "src/main.py" in index.records


def test_scan_repository_ignores_claude_directory(tmp_path: Path) -> None:
    """`.claude/` (Claude Code settings + worktree checkouts) is skipped wholesale.

    Without this exclusion, running `repoctx index` from a repo with active
    Claude Code worktrees double-counts every file that exists in both the
    parent repo and `.claude/worktrees/<name>/`.
    """
    write_file(
        tmp_path / ".claude" / "worktrees" / "branch-x" / "src" / "main.py",
        "def dup():\n    return True\n",
    )
    write_file(tmp_path / ".claude" / "settings.json", '{"theme": "dark"}\n')
    write_file(tmp_path / "src" / "main.py", "def run():\n    return True\n")

    index = scan_repository(tmp_path)

    assert ".claude/worktrees/branch-x/src/main.py" not in index.records
    assert ".claude/settings.json" not in index.records
    assert "src/main.py" in index.records
