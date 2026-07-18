"""Test configuration for preferring the workspace checkout over installed builds."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
repo_root_str = str(REPO_ROOT)

if repo_root_str not in sys.path:
    sys.path.insert(0, repo_root_str)


@pytest.fixture(autouse=True)
def _no_ambient_autoprovision(monkeypatch: pytest.MonkeyPatch):
    """Keep zero-setup provisioning inert unless a test opts in.

    The suite itself runs inside cloud containers where CLAUDE_CODE_REMOTE=true
    — exactly the signal that (correctly) arms background provisioning in
    production. Left armed under pytest, every create_server()/protocol-op call
    would spawn real provisioning threads against tmp repos: auto-granting
    index consent (breaking the consent-prompt tests), appending telemetry
    events, and kicking 60s model loads. Tests that exercise the gate set the
    env explicitly, which overrides this baseline.
    """
    monkeypatch.delenv("CLAUDE_CODE_REMOTE", raising=False)
    monkeypatch.setenv("REPOCTX_AUTO_EMBEDDINGS", "0")


@pytest.fixture(autouse=True)
def _no_ambient_reporting(monkeypatch: pytest.MonkeyPatch):
    """Keep upload reporting inert suite-wide unless a test opts in.

    ``DEFAULT_ENDPOINT`` is the real production ingest URL, and the enqueue
    path now kicks a background flush. A maintainer running the suite with
    ``REPOCTX_DOGFOOD=1`` exported (the documented dogfood setup) would
    otherwise POST synthetic test events straight to production. Hard-off here;
    ``test_reporting.py`` deletes this var to exercise the real precedence
    rules, and additionally pins autoflush off so it never opens a socket.
    """
    monkeypatch.delenv("REPOCTX_DOGFOOD", raising=False)
    monkeypatch.setenv("REPOCTX_REPORTING", "off")
    monkeypatch.setenv("REPOCTX_REPORTING_AUTOFLUSH", "off")
