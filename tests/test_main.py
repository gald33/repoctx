import json
import sys
from pathlib import Path

import pytest

from repoctx import main as repoctx_main


def test_cli_writes_repoctx_telemetry(tmp_path: Path, monkeypatch, capsys) -> None:
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    telemetry_dir = tmp_path / ".telemetry"

    monkeypatch.setenv("REPOCTX_TELEMETRY_DIR", str(telemetry_dir))
    monkeypatch.setattr(sys, "argv", ["repoctx", "demo task", "--repo", str(tmp_path), "--format", "json"])

    repoctx_main.main()

    stdout = capsys.readouterr().out
    payload = json.loads(stdout)
    assert "metrics" not in payload
    event_path = telemetry_dir / "repoctx-events.jsonl"
    assert event_path.exists()
    telemetry_payload = json.loads(event_path.read_text(encoding="utf-8").strip())
    assert "query" not in telemetry_payload
    assert "repo_root" not in telemetry_payload


def test_cli_records_failure_telemetry_and_exits(tmp_path: Path, monkeypatch) -> None:
    telemetry_dir = tmp_path / ".telemetry"
    missing_repo = tmp_path / "missing"

    monkeypatch.setenv("REPOCTX_TELEMETRY_DIR", str(telemetry_dir))
    monkeypatch.setattr(sys, "argv", ["repoctx", "demo task", "--repo", str(missing_repo)])

    with pytest.raises(SystemExit) as exc_info:
        repoctx_main.main()

    assert exc_info.value.code == 1
    event_path = telemetry_dir / "repoctx-events.jsonl"
    assert event_path.exists()
    telemetry_payload = json.loads(event_path.read_text(encoding="utf-8").strip())
    assert "query" not in telemetry_payload
    assert "repo_root" not in telemetry_payload
