from pathlib import Path


def test_tooling_repoctx_layout_exists() -> None:
    project_root = Path(__file__).resolve().parents[1]

    assert (project_root / "pyproject.toml").exists()
    assert (project_root / "repoctx").is_dir()
    assert (project_root / "repoctx" / "mcp_server.py").exists()
