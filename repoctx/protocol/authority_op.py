"""authority(task) — authority records + constraints only."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from repoctx.bundle import build_bundle

Include = Literal["summary", "full"]


def op_authority(
    task: str,
    repo_root: str | Path = ".",
    *,
    include: Include = "summary",
) -> dict[str, Any]:
    bundle = build_bundle(task, repo_root=repo_root)
    payload = bundle.to_dict(include_full_text=(include == "full"))
    return {
        "schema_version": payload["schema_version"],
        "task": payload["task"],
        "authority": payload["authority"],
        "uncertainty_rule": payload["uncertainty_rule"],
    }
