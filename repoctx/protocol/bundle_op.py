"""bundle(task) — primary protocol op."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from repoctx.bundle import build_bundle


def op_bundle(task: str, repo_root: str | Path = ".", *, include_full_text: bool = False) -> dict[str, Any]:
    bundle = build_bundle(task, repo_root=repo_root)
    return bundle.to_dict(include_full_text=include_full_text)
