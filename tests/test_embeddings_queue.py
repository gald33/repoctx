"""Tests for the debounced embedding-update queue.

These exercise the queue file format, dedupe, threshold/age triggers, and
crash recovery without loading any embedding model. ``update_file_in_index``
is monkeypatched to a no-op recorder so we never touch sentence-transformers.
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path

import pytest

from repoctx import embeddings as emb
from repoctx.config import DEFAULT_EMBEDDING_CONFIG


@pytest.fixture
def fast_config():
    """Threshold of 3, max age 60s — small enough to drive deterministically."""
    return replace(DEFAULT_EMBEDDING_CONFIG, debounce_n=3, debounce_max_age_seconds=60)


@pytest.fixture
def patched_update(monkeypatch):
    calls: list[str] = []

    def fake_update(file_path, repo_root, config=DEFAULT_EMBEDDING_CONFIG):
        calls.append(str(file_path))

    monkeypatch.setattr(emb, "update_file_in_index", fake_update)
    return calls


def test_enqueue_writes_jsonl(tmp_path: Path, fast_config, patched_update):
    result = emb.enqueue_for_update("src/foo.py", repo_root=tmp_path, config=fast_config)
    assert result == {"queued": "src/foo.py", "flushed": 0}

    queue = tmp_path / fast_config.index_dir / "embeddings" / fast_config.queue_filename
    lines = queue.read_text().strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["path"] == "src/foo.py"
    assert "queued_at" in entry


def test_threshold_triggers_flush(tmp_path: Path, fast_config, patched_update):
    for i in range(2):
        result = emb.enqueue_for_update(f"src/f{i}.py", repo_root=tmp_path, config=fast_config)
        assert result["flushed"] == 0
    final = emb.enqueue_for_update("src/f2.py", repo_root=tmp_path, config=fast_config)
    assert final["flushed"] == 3
    assert sorted(patched_update) == ["src/f0.py", "src/f1.py", "src/f2.py"]
    queue = tmp_path / fast_config.index_dir / "embeddings" / fast_config.queue_filename
    assert not queue.exists() or queue.stat().st_size == 0


def test_dedupe_keeps_latest_per_path(tmp_path: Path, fast_config, patched_update):
    emb.enqueue_for_update("src/foo.py", repo_root=tmp_path, config=fast_config)
    emb.enqueue_for_update("src/foo.py", repo_root=tmp_path, config=fast_config)
    emb.enqueue_for_update("src/bar.py", repo_root=tmp_path, config=fast_config)
    # 3 raw entries, but only 2 unique paths — threshold (3) not yet met
    status = emb.pending_status(repo_root=tmp_path, config=fast_config)
    assert status["count"] == 2
    assert patched_update == []


def test_age_triggers_flush(tmp_path: Path, patched_update):
    config = replace(DEFAULT_EMBEDDING_CONFIG, debounce_n=99, debounce_max_age_seconds=0)
    result = emb.enqueue_for_update("src/foo.py", repo_root=tmp_path, config=config)
    assert result["flushed"] == 1
    assert patched_update == ["src/foo.py"]


def test_force_flush(tmp_path: Path, fast_config, patched_update):
    emb.enqueue_for_update("src/foo.py", repo_root=tmp_path, config=fast_config)
    n = emb.flush_pending(repo_root=tmp_path, config=fast_config)
    assert n == 1
    assert patched_update == ["src/foo.py"]


def test_flush_is_noop_when_empty(tmp_path: Path, fast_config, patched_update):
    n = emb.flush_pending(repo_root=tmp_path, config=fast_config)
    assert n == 0
    assert patched_update == []


def test_pending_status_empty_repo(tmp_path: Path, fast_config):
    status = emb.pending_status(repo_root=tmp_path, config=fast_config)
    assert status == {"count": 0, "oldest_age_seconds": 0.0, "paths": []}


def test_failed_embed_is_requeued(tmp_path: Path, fast_config, monkeypatch):
    """A path whose embed raises a non-FileNotFound exception stays queued."""
    attempts: list[str] = []

    def flaky_update(file_path, repo_root, config=DEFAULT_EMBEDDING_CONFIG):
        attempts.append(str(file_path))
        if str(file_path) == "src/bad.py":
            raise RuntimeError("boom")

    monkeypatch.setattr(emb, "update_file_in_index", flaky_update)
    emb.enqueue_for_update("src/good.py", repo_root=tmp_path, config=fast_config)
    emb.enqueue_for_update("src/bad.py", repo_root=tmp_path, config=fast_config)
    emb.flush_pending(repo_root=tmp_path, config=fast_config)

    status = emb.pending_status(repo_root=tmp_path, config=fast_config)
    assert status["count"] == 1
    assert status["paths"] == ["src/bad.py"]
    assert "src/good.py" in attempts and "src/bad.py" in attempts


def test_missing_file_is_dropped(tmp_path: Path, fast_config, monkeypatch):
    def fnf(file_path, repo_root, config=DEFAULT_EMBEDDING_CONFIG):
        raise FileNotFoundError(file_path)

    monkeypatch.setattr(emb, "update_file_in_index", fnf)
    emb.enqueue_for_update("src/gone.py", repo_root=tmp_path, config=fast_config)
    emb.flush_pending(repo_root=tmp_path, config=fast_config)
    status = emb.pending_status(repo_root=tmp_path, config=fast_config)
    assert status["count"] == 0


def test_crash_recovery_picks_up_flushing_file(tmp_path: Path, fast_config, patched_update):
    """A leftover ``.pending.flushing`` from a crashed run is replayed on next enqueue."""
    emb_dir = tmp_path / fast_config.index_dir / "embeddings"
    emb_dir.mkdir(parents=True)
    flushing = emb_dir / (fast_config.queue_filename + ".flushing")
    flushing.write_text(
        json.dumps({"path": "src/orphan.py", "queued_at": time.time()}) + "\n"
    )

    emb.enqueue_for_update("src/new.py", repo_root=tmp_path, config=fast_config)
    # The orphan got merged into pending; size threshold not yet hit (2 unique paths).
    status = emb.pending_status(repo_root=tmp_path, config=fast_config)
    assert status["count"] == 2
    assert "src/orphan.py" in status["paths"]
