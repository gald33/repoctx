from pathlib import Path

from repoctx.stats import compute_stats, render_markdown
from repoctx.telemetry import record_index_build


def _seed_builds(telemetry_dir: Path) -> None:
    # Two builds with a model-load-dominated profile (the case we care about:
    # the embed is cheap, the model load/download is the cost).
    for embed_ms in (4_000, 6_000):
        record_index_build(
            telemetry_dir=telemetry_dir,
            session_id="s",
            surface="cli",
            repo_root=telemetry_dir,  # any stable path; hashed at write time
            success=True,
            duration_ms=8_000 + embed_ms,
            source="origin-main",
            incremental=False,
            chunk_count=456,
            file_count=95,
            embedded_chunk_count=456,
            model_load_ms=8_000,
            embed_ms=embed_ms,
            scan_ms=200,
            device="cpu",
            dtype="fp32",
            model_name="Qwen/Qwen3-Embedding-0.6B",
            output_bytes=1_870_000,
        )


def test_index_build_events_surface_in_stats(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("REPOCTX_REPORTING", "off")  # hermetic: no upload queue
    telemetry_dir = tmp_path / "telemetry"
    _seed_builds(telemetry_dir)

    stats = compute_stats(telemetry_dir=telemetry_dir, days=None)

    # Auto per-op aggregation keys off event_type + duration_ms.
    ops = {row["op"]: row for row in stats["by_op"]}
    assert "index_build" in ops
    assert ops["index_build"]["count"] == 2

    # Dedicated breakdown isolates the model-load vs embed split.
    ib = stats["index_builds"]
    assert ib["count"] == 2
    assert ib["success_count"] == 2
    assert ib["model_load_ms"]["p50"] == 8_000
    assert ib["embed_ms"]["max"] == 6_000
    assert ib["file_count"]["p50"] == 95


def test_index_build_breakdown_renders_markdown(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("REPOCTX_REPORTING", "off")
    telemetry_dir = tmp_path / "telemetry"
    _seed_builds(telemetry_dir)

    md = render_markdown(compute_stats(telemetry_dir=telemetry_dir, days=None))

    assert "## Index builds" in md
    assert "model load" in md
    assert "embed" in md
