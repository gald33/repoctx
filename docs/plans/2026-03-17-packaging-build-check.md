# Packaging Build Check Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a pytest-enforced build check that proves `repoctx` can produce both a wheel and an sdist.

**Architecture:** Add a dedicated packaging test that shells out to `python -m build` and writes artifacts into a pytest temporary directory. Update the dev dependency set so the test environment includes the `build` package required to exercise the real release path.

**Tech Stack:** Python 3.11+, pytest, setuptools, build

---

### Task 1: Add the failing packaging test

**Files:**
- Create: `tests/test_packaging.py`
- Test: `tests/test_packaging.py`

**Step 1: Write the failing test**

```python
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
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_packaging.py -q`
Expected: FAIL because `python -m build` is unavailable in the dev environment.

### Task 2: Add the minimal packaging dependency

**Files:**
- Modify: `pyproject.toml`
- Test: `tests/test_packaging.py`

**Step 3: Write minimal implementation**

Add `"build>=1.2.0"` to `[project.optional-dependencies].dev`.

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_packaging.py -q`
Expected: PASS with one generated wheel and one generated sdist.

### Task 3: Verify the full suite

**Files:**
- Test: `tests/`

**Step 5: Run the full test suite**

Run: `python3 -m pytest -q`
Expected: PASS.
