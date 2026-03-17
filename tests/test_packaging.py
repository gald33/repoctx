from pathlib import Path
import subprocess
import sys


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
    assert len(list(tmp_path.glob("repoctx-*.tar.gz"))) == 1
    assert len(list(tmp_path.glob("repoctx-*.whl"))) == 1
