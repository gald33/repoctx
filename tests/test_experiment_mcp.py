import json
from pathlib import Path

import pytest

from repoctx import experiment_mcp as em


def _write_config(path: Path, **overrides: object) -> None:
    base = {
        "experiment_mcp_suppress": True,
        "experiment_mcp_idle_ttl_seconds": 30,
        "experiment_mcp_extend_seconds": 120,
    }
    base.update(overrides)
    path.write_text(json.dumps(base), encoding="utf-8")


def test_arm_and_expire_suppression(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.json"
    _write_config(cfg, experiment_mcp_idle_ttl_seconds=30)
    monkeypatch.setenv("REPOCTX_CONFIG_PATH", str(cfg))
    telem = tmp_path / ".t"
    monkeypatch.setenv("REPOCTX_TELEMETRY_DIR", str(telem))

    clock = [1_000_000.0]
    monkeypatch.setattr("repoctx.experiment_mcp.time.time", lambda: clock[0])

    assert em.arm_control_lane_mcp_suppression(telemetry_dir=telem) is True
    assert em.mcp_suppression_should_short_circuit(telemetry_dir=telem) is True

    clock[0] = 1_000_100.0
    em.refresh_after_cli_invocation(telemetry_dir=telem)
    assert em.mcp_suppression_should_short_circuit(telemetry_dir=telem) is False
    assert not (telem / em.STATE_FILENAME).exists()


def test_cli_refresh_extends_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.json"
    _write_config(cfg, experiment_mcp_idle_ttl_seconds=30, experiment_mcp_extend_seconds=600)
    monkeypatch.setenv("REPOCTX_CONFIG_PATH", str(cfg))
    telem = tmp_path / ".t"
    monkeypatch.setenv("REPOCTX_TELEMETRY_DIR", str(telem))

    clock = [1_000_000.0]
    monkeypatch.setattr("repoctx.experiment_mcp.time.time", lambda: clock[0])

    em.arm_control_lane_mcp_suppression(telemetry_dir=telem)

    clock[0] = 1_000_020.0
    em.refresh_after_cli_invocation(telemetry_dir=telem)
    assert em.mcp_suppression_should_short_circuit(telemetry_dir=telem) is True

    clock[0] = 1_000_050.0
    assert em.mcp_suppression_should_short_circuit(telemetry_dir=telem) is True

    clock[0] = 1_001_000.0
    em.refresh_after_cli_invocation(telemetry_dir=telem)
    assert em.mcp_suppression_should_short_circuit(telemetry_dir=telem) is False


def test_arm_skipped_when_user_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "config.json"
    _write_config(cfg, experiment_mcp_suppress=False)
    monkeypatch.setenv("REPOCTX_CONFIG_PATH", str(cfg))
    telem = tmp_path / ".t"
    monkeypatch.setenv("REPOCTX_TELEMETRY_DIR", str(telem))

    assert em.arm_control_lane_mcp_suppression(telemetry_dir=telem) is False
    assert em.mcp_suppression_should_short_circuit(telemetry_dir=telem) is False
