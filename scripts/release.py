#!/usr/bin/env python3
"""Build a stable or canary wheel of repoctx-mcp.

**Primary use is via CI.** Real releases go through GitHub Actions +
PyPI Trusted Publishing (OIDC) — see ``.github/workflows/publish-pypi.yml``.
Do not run ``--upload`` locally; the AGENTS.md release section explains why.

This script is used in two contexts:

1. **CI (canary path)**: GHA invokes ``--channel canary --prepare-only``
   to rewrite ``_build_channel.py`` and ``pyproject.toml`` so the next
   ``python -m build`` step picks up the canary metadata. CI then publishes
   via OIDC.

2. **Local testing**: developers run ``--dry-run`` to preview the version
   that would be generated, or run without ``--upload`` to produce a local
   wheel under ``dist/`` for smoke-testing.

Behavior:

  1. Rewrites ``repoctx/_build_channel.py`` with the chosen ``CHANNEL`` and
     a generated ``BUILD_ID`` (``<version>+<channel>.<sha>``).
  2. Rewrites the ``version`` in ``pyproject.toml`` — verbatim for stable,
     with a PEP 440 ``.devN`` suffix for canary (timestamped so each canary
     wheel sorts after the previous one).
  3. Runs ``python -m build`` to produce wheel + sdist under ``dist/``
     (skipped when ``--prepare-only`` is set).
  4. Optionally ``twine upload dist/*`` (only with ``--upload``; not the
     recommended path — CI is).
  5. Restores ``pyproject.toml`` and ``_build_channel.py`` to their
     pre-script contents — always, even on failure — UNLESS
     ``--prepare-only`` is set (CI needs the rewrites to persist for the
     following build step).

Usage::

    # CI canary path (prepare files, exit; CI runs `python -m build` next)
    python scripts/release.py --channel canary --prepare-only --skip-clean-check

    # Local canary build for testing the wheel (no upload, restores files)
    python scripts/release.py --channel canary

    # Preview what would happen without writing anything
    python scripts/release.py --channel canary --dry-run

    # Local stable build for testing — DO NOT --upload locally
    python scripts/release.py --channel stable

Canary wheels are published to the same PyPI package (``repoctx-mcp``) as
pre-releases; users opt in with ``pip install --pre repoctx-mcp``.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
BUILD_CHANNEL = REPO_ROOT / "repoctx" / "_build_channel.py"
DIST_DIR = REPO_ROOT / "dist"


def read_pyproject_version() -> str:
    with PYPROJECT.open("rb") as handle:
        data = tomllib.load(handle)
    return data["project"]["version"]


def write_pyproject_version(version: str) -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    # Match the first top-level ``version = "..."`` line. pyproject.toml
    # only has one such line under [project]; build-system uses
    # ``requires``, not ``version``.
    new_text, n = re.subn(
        r'^version\s*=\s*"[^"]+"',
        f'version = "{version}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if n != 1:
        raise RuntimeError(
            f"Could not find a top-level `version = \"...\"` line in {PYPROJECT}"
        )
    PYPROJECT.write_text(new_text, encoding="utf-8")


def write_build_channel(channel: str, build_id: str) -> None:
    text = BUILD_CHANNEL.read_text(encoding="utf-8")

    new_text, n_channel = re.subn(
        r'^CHANNEL: Channel = "[^"]+"',
        f'CHANNEL: Channel = "{channel}"',
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if n_channel != 1:
        raise RuntimeError(
            f"Could not find `CHANNEL: Channel = \"...\"` in {BUILD_CHANNEL}"
        )

    new_text, n_build = re.subn(
        r'^BUILD_ID: str = "[^"]+"',
        f'BUILD_ID: str = "{build_id}"',
        new_text,
        count=1,
        flags=re.MULTILINE,
    )
    if n_build != 1:
        raise RuntimeError(
            f"Could not find `BUILD_ID: str = \"...\"` in {BUILD_CHANNEL}"
        )

    BUILD_CHANNEL.write_text(new_text, encoding="utf-8")


def git_short_sha() -> str:
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def git_is_clean() -> bool:
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip() == ""


def strip_dev_suffix(version: str) -> str:
    """``1.5.0.dev20260526142231`` -> ``1.5.0``. Idempotent for stable versions."""
    if ".dev" in version:
        return version.split(".dev", 1)[0]
    return version


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a stable or canary release of repoctx-mcp.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--channel",
        required=True,
        choices=["stable", "canary"],
        help="Which channel to bake into the wheel.",
    )
    parser.add_argument(
        "--version",
        default=None,
        help="Base version to release. Defaults to the version in pyproject.toml.",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload to PyPI via twine after a successful build.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen without writing files or building.",
    )
    parser.add_argument(
        "--skip-clean-check",
        action="store_true",
        help="Allow a dirty working tree. Useful in CI; risky locally.",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help=(
            "CI mode: rewrite the build files and exit. Does NOT build, "
            "upload, or restore originals — CI's next step handles the build, "
            "and the files don't need to be restored because the CI workspace "
            "is throwaway."
        ),
    )
    args = parser.parse_args()

    if not args.skip_clean_check and not args.dry_run and not git_is_clean():
        print(
            "ERROR: working tree is dirty. Commit or stash first, "
            "or pass --skip-clean-check.",
            file=sys.stderr,
        )
        return 2

    base_version = strip_dev_suffix(args.version or read_pyproject_version())
    now = _dt.datetime.now(_dt.timezone.utc)
    sha = git_short_sha()

    if args.channel == "stable":
        package_version = base_version
        build_id = f"{base_version}+stable.{now:%Y%m%d}.{sha}"
    else:
        dev_n = now.strftime("%Y%m%d%H%M%S")
        package_version = f"{base_version}.dev{dev_n}"
        build_id = f"{package_version}+canary.{sha}"

    print(f"Channel:         {args.channel}")
    print(f"Base version:    {base_version}")
    print(f"Package version: {package_version}")
    print(f"Build ID:        {build_id}")
    print(f"Will upload:     {args.upload}")
    print()

    if args.dry_run:
        print("--dry-run: no files written, no build run.")
        return 0

    if args.prepare_only:
        # CI path: rewrite, exit, leave files for the next step.
        write_pyproject_version(package_version)
        write_build_channel(args.channel, build_id)
        print(f"\nPrepared {package_version} on channel '{args.channel}'.")
        print("pyproject.toml and _build_channel.py have been rewritten in place.")
        print("Next step in CI: `python -m build`.")
        return 0

    pyproject_backup = PYPROJECT.read_text(encoding="utf-8")
    build_channel_backup = BUILD_CHANNEL.read_text(encoding="utf-8")

    try:
        write_pyproject_version(package_version)
        write_build_channel(args.channel, build_id)

        if DIST_DIR.exists():
            shutil.rmtree(DIST_DIR)

        subprocess.run(
            [sys.executable, "-m", "build"],
            cwd=REPO_ROOT,
            check=True,
        )

        artifacts = sorted(DIST_DIR.glob("*"))
        print("\nArtifacts:")
        for path in artifacts:
            print(f"  {path.relative_to(REPO_ROOT)}")

        if args.upload:
            print(
                "\nWARNING: --upload uses twine locally. CI (via PyPI Trusted "
                "Publishing) is the recommended path. Continuing because you "
                "asked for it…"
            )
            subprocess.run(
                ["twine", "upload", *[str(p) for p in artifacts]],
                cwd=REPO_ROOT,
                check=True,
            )
    finally:
        PYPROJECT.write_text(pyproject_backup, encoding="utf-8")
        BUILD_CHANNEL.write_text(build_channel_backup, encoding="utf-8")
        print("\nRestored pyproject.toml and _build_channel.py.")

    print(f"\nDone: {package_version} built on channel '{args.channel}'.")
    if args.channel == "canary" and not args.upload:
        print(
            "\nThis canary wheel is local-only. To publish, trigger the CI "
            "canary workflow: "
            "`gh workflow run publish-pypi.yml -f channel=canary`. "
            "Users install canary builds via `pip install --pre repoctx-mcp`."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
