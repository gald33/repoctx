"""Tests for the subkind classifier and the hierarchical threshold lookup."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repoctx.config import DEFAULT_CONFIG, RepoCtxConfig
from repoctx.config_loader import load_repo_config
from repoctx.subkinds import classify_subkind, full_kind, parent_kind


# --- classifier basics ------------------------------------------------------


def test_unknown_kind_returns_empty_subkind():
    assert classify_subkind("test", "tests/test_x.py", "") == ""
    assert classify_subkind("other", "weird.bin", "") == ""


def test_full_kind_and_parent_kind_round_trip():
    assert full_kind("code", "handler") == "code/handler"
    assert full_kind("code", "") == "code"
    assert parent_kind("code/handler") == "code"
    assert parent_kind("code") == "code"


# --- code subkinds ----------------------------------------------------------


def test_code_handler_by_path():
    assert classify_subkind("code", "src/api/users.py", "") == "handler"
    assert classify_subkind("code", "app/routes/login.ts", "") == "handler"


def test_code_handler_by_import():
    assert classify_subkind("code", "flat/app.py", "from fastapi import FastAPI\n") == "handler"
    assert classify_subkind("code", "server.js", "import express from 'express';\n") == "handler"


def test_code_model_by_path():
    assert classify_subkind("code", "src/models/user.py", "") == "model"
    assert classify_subkind("code", "lib/schemas/order.ts", "") == "model"


def test_code_model_by_import():
    content = "from pydantic import BaseModel\n\nclass User(BaseModel):\n    name: str\n"
    assert classify_subkind("code", "flat/x.py", content) == "model"


def test_code_cli_by_path_and_import():
    assert classify_subkind("code", "src/cli/run.py", "") == "cli"
    assert classify_subkind("code", "flat/main.py", "import argparse\n") == "cli"


def test_code_util_by_path():
    assert classify_subkind("code", "src/utils/strings.py", "") == "util"
    assert classify_subkind("code", "lib/helpers.py", "") == "util"


def test_code_scaffold_by_name():
    assert classify_subkind("code", "src/__init__.py", "") == "scaffold"
    assert classify_subkind("code", "tests/conftest.py", "") == "scaffold"
    assert classify_subkind("code", "setup.py", "") == "scaffold"


def test_code_generated_overrides_path():
    # A model-style file with a generated marker → generated, not model.
    content = "# AUTO-GENERATED — DO NOT EDIT\nfrom pydantic import BaseModel\n"
    assert classify_subkind("code", "src/models/proto.py", content) == "generated"


def test_code_pure_function_module_is_util():
    # No class, no decorator, no framework import → util fallback.
    content = "def helper(x):\n    return x + 1\n"
    assert classify_subkind("code", "flat/thing.py", content) == "util"


def test_code_other_when_nothing_matches():
    content = "class Thing:\n    pass\n"
    assert classify_subkind("code", "flat/thing.py", content) == "other"


# --- doc / config subkinds --------------------------------------------------


def test_doc_agent_contract():
    assert classify_subkind("doc", "AGENTS.md", "") == "agent_contract"
    assert classify_subkind("doc", "CLAUDE.md", "") == "agent_contract"


def test_doc_architecture():
    assert classify_subkind("doc", "docs/architecture/auth.md", "") == "architecture"
    assert classify_subkind("doc", "docs/adr/0001-storage.md", "") == "architecture"


def test_doc_readme():
    assert classify_subkind("doc", "README.md", "") == "readme"
    assert classify_subkind("doc", "src/auth/README.md", "") == "readme"


def test_config_build():
    assert classify_subkind("config", "pyproject.toml", "") == "build"
    assert classify_subkind("config", "package.json", "") == "build"


def test_config_ci():
    assert classify_subkind("config", ".github/workflows/test.yml", "") == "ci"


def test_config_lint():
    assert classify_subkind("config", ".eslintrc.json", "") == "lint"
    assert classify_subkind("config", "ruff.toml", "") == "lint"


# --- hierarchical threshold lookup -----------------------------------------


def test_lookup_falls_back_subkind_to_parent_to_default():
    cfg = RepoCtxConfig(
        embedding_qualify_thresholds={
            "_default": 0.4,
            "code": 0.3,
            "code/handler": 0.22,
        },
    )
    # exact subkind match
    assert cfg.qualify_threshold_for("code", "handler") == 0.22
    # subkind missing → falls to parent kind
    assert cfg.qualify_threshold_for("code", "util") == 0.3
    # parent missing → falls to _default
    assert cfg.qualify_threshold_for("doc", "readme") == 0.4


def test_lookup_works_with_empty_subkind():
    cfg = RepoCtxConfig(
        embedding_qualify_thresholds={"_default": 0.4, "code": 0.3, "code/handler": 0.22},
    )
    assert cfg.qualify_threshold_for("code", "") == 0.3
    assert cfg.qualify_threshold_for("code") == 0.3


def test_loader_accepts_subkind_keys(tmp_path: Path):
    (tmp_path / ".repoctx").mkdir()
    (tmp_path / ".repoctx" / "config.json").write_text(json.dumps({
        "embedding_qualify_thresholds": {
            "code/handler": 0.18,
            "code/model": 0.28,
            "code": 0.32,
        }
    }))
    loaded = load_repo_config(tmp_path)
    assert loaded.qualify_threshold_for("code", "handler") == 0.18
    assert loaded.qualify_threshold_for("code", "model") == 0.28
    assert loaded.qualify_threshold_for("code", "util") == 0.32  # falls back to parent
    # Default thresholds for other kinds remain at the built-in default.
    assert loaded.qualify_threshold_for("doc", "readme") == DEFAULT_CONFIG.qualify_threshold_for("doc")


def test_loader_rejects_invalid_subkind_parent(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    (tmp_path / ".repoctx").mkdir()
    (tmp_path / ".repoctx" / "config.json").write_text(json.dumps({
        "embedding_qualify_thresholds": {"banana/peel": 0.5},
    }))
    with caplog.at_level("WARNING"):
        loaded = load_repo_config(tmp_path)
    # banana/peel was rejected — defaults unchanged
    assert loaded.qualify_threshold_for("banana") == DEFAULT_CONFIG.qualify_threshold_for("_default")
    assert any("banana/peel" in r.message for r in caplog.records)
