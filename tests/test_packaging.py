from pathlib import Path
import subprocess
import sys
import tarfile
import tomllib
import zipfile

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

    with tarfile.open(sdists[0], "r:gz") as sdist_archive:
        sdist_names = sdist_archive.getnames()
    assert any(name.endswith("docs/man/repoctx.1") for name in sdist_names)

    with zipfile.ZipFile(wheels[0]) as wheel_archive:
        wheel_names = wheel_archive.namelist()
    assert any(name.endswith("share/man/man1/repoctx.1") for name in wheel_names)


def test_man_page_documents_default_query_and_explicit_query_flags() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    man_page = (repo_root / "docs" / "man" / "repoctx.1").read_text(encoding="utf-8")

    assert 'repoctx "refactor the auth middleware to support OAuth"' in man_page
    assert "repoctx query" in man_page
    assert "--repo /path/to/repo --format json" in man_page
