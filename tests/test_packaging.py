from pathlib import Path
import subprocess
import sys
import tomllib

DIST_NAME_PREFIXES = ("repoctx-mcp-", "repoctx_mcp-")


def test_mcp_dependency_is_in_base_install() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))

    dependencies = pyproject["project"]["dependencies"]

    assert any(dep.startswith("mcp>=") for dep in dependencies)


def test_build_produces_wheel_and_sdist(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "build", "--sdist", "--wheel", "--outdir", str(tmp_path)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    sdists = list(tmp_path.glob("*.tar.gz"))
    wheels = list(tmp_path.glob("*.whl"))

    assert len(sdists) == 1
    assert len(wheels) == 1
    assert sdists[0].name.startswith(DIST_NAME_PREFIXES)
    assert wheels[0].name.startswith(DIST_NAME_PREFIXES)
