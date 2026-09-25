"""A large module is searchable past its first `max_file_bytes`, and shell/SQL are indexed.

Before 1.17.0 every reader sliced a file to 16 KB before the chunker ever saw it,
so in a large module — the hub files most questions are about — everything
past the cut was absent from the embedding index. Measured on one production
repo: 298 of 1,527 code files over the cap, 42% of code bytes unsearchable.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from repoctx.chunker import ChunkConfig
from repoctx.config import DEFAULT_CONFIG
from repoctx.embeddings import _chunks_for_record
from repoctx.git_tree import scan_git_tree
from repoctx.scanner import scan_repository

CAP = DEFAULT_CONFIG.max_file_bytes
LATE = "def reconcile_ledger_after_the_cut(account):\n    return account.balance\n"


def _big_module() -> str:
    filler = "".join(f"def helper_{i}(x):\n    return x + {i}\n\n" for i in range(2000))
    assert len(filler) > CAP, "fixture must exceed the lexical cap"
    return "import os\n\n" + filler + "import late_dependency\n\n" + LATE


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_lexical_view_stays_capped_but_full_text_is_kept(tmp_path: Path) -> None:
    _write(tmp_path / "pkg" / "big.py", _big_module())
    _write(tmp_path / "pkg" / "small.py", "def tiny():\n    return 1\n")

    records = scan_repository(tmp_path).records
    big, small = records["pkg/big.py"], records["pkg/small.py"]

    assert len(big.content) == CAP
    assert "reconcile_ledger_after_the_cut" not in big.content
    assert "reconcile_ledger_after_the_cut" in big.full_content
    assert small.full_content == "", "an untruncated file keeps one copy of its text"


def test_chunker_reaches_code_past_the_cap(tmp_path: Path) -> None:
    _write(tmp_path / "big.py", _big_module())
    record = scan_repository(tmp_path).records["big.py"]

    chunks = _chunks_for_record(record, ChunkConfig())

    late = [c for c in chunks if "reconcile_ledger_after_the_cut" in c.text]
    assert late, "the function defined after 16 KB must land in some chunk"
    assert late[0].start_line > record.content.count("\n")


def test_git_tree_scan_keeps_full_text_and_late_imports(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    _write(tmp_path / "big.py", _big_module())
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "big")

    record = scan_git_tree(tmp_path, "HEAD").records["big.py"]

    assert len(record.content) == CAP
    assert "reconcile_ledger_after_the_cut" in record.full_content
    assert "late_dependency" in record.import_source


def test_shell_and_sql_are_indexed_as_code(tmp_path: Path) -> None:
    _write(tmp_path / "scripts" / "deploy.sh", "#!/usr/bin/env bash\nensure_disk_headroom() { df -BM /; }\n")
    _write(tmp_path / "db" / "001_init.sql", "CREATE TABLE jobs (id uuid primary key);\n")

    records = scan_repository(tmp_path).records

    assert records["scripts/deploy.sh"].kind == "code"
    assert records["db/001_init.sql"].kind == "code"
    assert _chunks_for_record(records["scripts/deploy.sh"], ChunkConfig())
