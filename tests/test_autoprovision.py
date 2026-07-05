"""Tests for zero-setup background provisioning of semantic retrieval."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from repoctx import autoprovision as ap
from repoctx.index_consent import read_consent, set_consent


@pytest.fixture()
def tmp_repo(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    return tmp_path


@pytest.fixture(autouse=True)
def _clean_process_state(monkeypatch: pytest.MonkeyPatch):
    """Each test gets a fresh in-process single-flight set and no env gates."""
    monkeypatch.setattr(ap, "_started_for", set())
    monkeypatch.delenv(ap.ENV_AUTO_EMBEDDINGS, raising=False)
    monkeypatch.delenv(ap.ENV_REMOTE_MARKER, raising=False)


# -- gating --------------------------------------------------------------------


def test_disabled_by_default_locally() -> None:
    assert ap.auto_provision_enabled() is False


def test_enabled_in_remote_container(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ap.ENV_REMOTE_MARKER, "true")
    assert ap.auto_provision_enabled() is True


def test_env_force_on_anywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ap.ENV_AUTO_EMBEDDINGS, "1")
    assert ap.auto_provision_enabled() is True


def test_kill_switch_beats_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ap.ENV_REMOTE_MARKER, "true")
    monkeypatch.setenv(ap.ENV_AUTO_EMBEDDINGS, "0")
    assert ap.auto_provision_enabled() is False


def test_maybe_start_noop_when_disabled(tmp_repo: Path) -> None:
    assert ap.maybe_start_auto_provision(tmp_repo) is False


# -- maybe_start_auto_provision ------------------------------------------------


def _force_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ap.ENV_AUTO_EMBEDDINGS, "1")


def test_start_spawns_provisioning_thread(
    tmp_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_enabled(monkeypatch)
    monkeypatch.setattr(ap, "_deps_importable", lambda: False)
    ran = threading.Event()
    monkeypatch.setattr(ap, "_provision", lambda root, td=None: ran.set())
    assert ap.maybe_start_auto_provision(tmp_repo) is True
    assert ran.wait(timeout=5)


def test_start_is_single_flight_per_process(
    tmp_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_enabled(monkeypatch)
    monkeypatch.setattr(ap, "_deps_importable", lambda: False)
    monkeypatch.setattr(ap, "_provision", lambda root, td=None: None)
    assert ap.maybe_start_auto_provision(tmp_repo) is True
    assert ap.maybe_start_auto_provision(tmp_repo) is False


def test_start_noop_when_everything_live(
    tmp_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_enabled(monkeypatch)
    monkeypatch.setattr(ap, "_deps_importable", lambda: True)
    monkeypatch.setattr(
        "repoctx.index_consent.is_index_present", lambda root: True
    )
    assert ap.maybe_start_auto_provision(tmp_repo) is False


def test_start_respects_declined_consent(
    tmp_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_enabled(monkeypatch)
    monkeypatch.setattr(ap, "_deps_importable", lambda: False)
    set_consent(tmp_repo, "declined")
    started = threading.Event()
    monkeypatch.setattr(ap, "_provision", lambda root, td=None: started.set())
    assert ap.maybe_start_auto_provision(tmp_repo) is False
    assert not started.wait(timeout=0.2)


def test_start_blocked_by_live_foreign_stamp(
    tmp_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_enabled(monkeypatch)
    monkeypatch.setattr(ap, "_deps_importable", lambda: False)
    ap._write_state(tmp_repo, "installing", "another process")
    assert ap.maybe_start_auto_provision(tmp_repo) is False


def test_stale_stamp_does_not_block(
    tmp_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _force_enabled(monkeypatch)
    monkeypatch.setattr(ap, "_deps_importable", lambda: False)
    ap._write_state(tmp_repo, "installing", "crashed run")
    # Age the stamp past the staleness window.
    path = ap._state_path(tmp_repo)
    state = json.loads(path.read_text())
    state["updated_at"] = time.time() - ap._STALE_STATE_SECONDS - 1
    path.write_text(json.dumps(state))
    monkeypatch.setattr(ap, "_provision", lambda root, td=None: None)
    assert ap.maybe_start_auto_provision(tmp_repo) is True


# -- _provision sequence ---------------------------------------------------------


def test_provision_full_sequence_records_consent_and_ready(
    tmp_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(ap, "_deps_importable", lambda: bool(calls))  # False → True after install
    monkeypatch.setattr(
        ap, "_install_embedding_deps", lambda: (calls.append("install"), (True, ""))[1]
    )
    monkeypatch.setattr(
        "repoctx.embeddings.refresh_base_index",
        lambda root, **kw: (calls.append("index"), {"status": "built"})[1],
    )
    status = ap._provision(tmp_repo)
    assert status == "ready"
    assert calls == ["install", "index"]
    assert read_consent(tmp_repo) == "granted"
    assert ap.provisioning_state(tmp_repo)["status"] == "ready"


def test_provision_stops_on_declined(tmp_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    set_consent(tmp_repo, "declined")
    monkeypatch.setattr(
        ap, "_install_embedding_deps",
        lambda: (_ for _ in ()).throw(AssertionError("must not install")),
    )
    assert ap._provision(tmp_repo) == "declined"


def test_provision_never_rewrites_explicit_grant(
    tmp_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_consent(tmp_repo, "granted")
    monkeypatch.setattr(ap, "_deps_importable", lambda: True)
    recorded: list[str] = []
    monkeypatch.setattr(ap, "_record_auto_consent", lambda *a, **k: recorded.append("x"))
    monkeypatch.setattr(
        "repoctx.embeddings.refresh_base_index", lambda root, **kw: {"status": "current"}
    )
    assert ap._provision(tmp_repo) == "ready"
    assert recorded == []  # consent was already answered; no auto-consent event


def test_provision_failed_install_journals_failure(
    tmp_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ap, "_deps_importable", lambda: False)
    monkeypatch.setattr(ap, "_install_embedding_deps", lambda: (False, "boom"))
    assert ap._provision(tmp_repo) == "failed"
    state = ap.provisioning_state(tmp_repo)
    assert state["status"] == "failed"
    assert "boom" in state["error"]
    # Consent must NOT have been auto-granted on a failed provision.
    assert read_consent(tmp_repo) is None


def test_provision_failed_index_build(tmp_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ap, "_deps_importable", lambda: True)
    monkeypatch.setattr(
        "repoctx.embeddings.refresh_base_index", lambda root, **kw: {"status": "error"}
    )
    assert ap._provision(tmp_repo) == "failed"


# -- install command construction ------------------------------------------------


def test_install_command_prefers_uv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ap.shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    cmd = ap._install_command()
    assert cmd[0] == "/usr/bin/uv"
    assert "--python" in cmd
    assert ap.INSTALL_SPEC in cmd
    assert ap.TORCH_CPU_INDEX in cmd


def test_install_command_falls_back_to_pip(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setattr(ap.shutil, "which", lambda name: None)
    cmd = ap._install_command()
    assert cmd[0] == sys.executable
    assert cmd[1:3] == ["-m", "pip"]
    assert ap.TORCH_CPU_INDEX in cmd


# -- status surfacing --------------------------------------------------------------


def test_provisioning_note_states(tmp_repo: Path) -> None:
    assert ap.provisioning_note(tmp_repo) == ""
    ap._write_state(tmp_repo, "installing", "installing deps")
    assert "provisioned automatically" in ap.provisioning_note(tmp_repo)
    ap._write_state(tmp_repo, "failed", "install", "no network")
    assert "no network" in ap.provisioning_note(tmp_repo)
    ap._write_state(tmp_repo, "ready", "index built")
    assert ap.provisioning_note(tmp_repo) == ""


def test_degraded_status_message_carries_provisioning_note(
    tmp_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repoctx import embeddings as emb

    ap._write_state(tmp_repo, "building_index", "embedding origin/main")
    monkeypatch.setattr(emb, "HAS_EMBEDDINGS", False)
    status = emb.probe_index_status(tmp_repo)
    assert status.status == emb.STATUS_DEPS_MISSING
    assert "provisioned automatically" in status.message


# -- embeddings availability refresh ----------------------------------------------


def test_refresh_embeddings_availability_flips_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulate the post-install re-probe: flags start False (as in a process
    that imported before the install), refresh flips them when deps import."""
    pytest.importorskip("sentence_transformers")
    from repoctx import embeddings as emb
    from repoctx import vector_index as vi

    monkeypatch.setattr(emb, "HAS_EMBEDDINGS", False)
    monkeypatch.setattr(emb, "SentenceTransformer", None)
    monkeypatch.setattr(emb, "_np", None)
    monkeypatch.setattr(vi, "HAS_NUMPY", False)
    monkeypatch.setattr(vi, "_np", None)

    assert emb.refresh_embeddings_availability() is True
    assert emb.HAS_EMBEDDINGS is True
    assert emb.SentenceTransformer is not None
    assert emb._np is not None
    assert vi.HAS_NUMPY is True
    assert vi._np is not None


def test_refresh_is_noop_when_already_available() -> None:
    pytest.importorskip("sentence_transformers")
    from repoctx import embeddings as emb

    if not emb.HAS_EMBEDDINGS:
        pytest.skip("embeddings not importable in this env")
    assert emb.refresh_embeddings_availability() is True


# -- MCP server wiring --------------------------------------------------------------


def test_run_op_kicks_autoprovision(
    tmp_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A protocol op call must trigger the (gated) provisioning check."""
    from repoctx.mcp_server import create_server

    kicked: list[Path] = []
    monkeypatch.setattr(
        ap, "maybe_start_auto_provision",
        lambda root, telemetry_dir=None: kicked.append(Path(root)) or False,
    )
    server = create_server(repo_root=tmp_repo)
    tool = next(t for t in server._tool_manager.list_tools() if t.name == "scope")
    tool.fn(task="anything")
    assert tmp_repo.resolve() in [p.resolve() for p in kicked]
