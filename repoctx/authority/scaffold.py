"""Scaffold a starter authority layout in a repository.

Writes:

- ``contracts/README.md`` explaining the convention
- ``contracts/example.md`` with front-matter + ``## Invariants`` + ``## Do not``
- ``docs/architecture/README.md`` explaining the convention
- ``docs/architecture/example.md`` as a Level-2 architecture note
- ``examples/README.md`` with a minimal note

Idempotent: existing files are preserved, never overwritten. Returns a list
of created paths so callers (CLI) can report what changed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

CONTRACTS_README = """# Contracts

Level-1 (hard) authority for this repository. Every file in this directory is
treated as an authoritative contract by `repoctx`.

## Conventions

Each contract file is markdown with an optional YAML-ish front-matter block:

```
---
id: auth/session-tokens
severity: hard
applies_to:
  - app/auth/**
validated_by:
  - tests/contracts/test_session_tokens.py
---
```

Under the body, list the rules that govern behavior:

```
## Invariants
- tokens must be encrypted at rest
- sessions must expire within 24 hours

## Do not
- log token values
```

Each bullet becomes one `Constraint` surfaced in `repoctx.bundle(task)`.
"""

CONTRACTS_EXAMPLE = """---
id: example/sample
severity: hard
applies_to:
  - src/example/**
---

# Example contract

Delete this file once you have real contracts.

## Invariants
- replace this with a real rule

## Do not
- ship this placeholder to production
"""

ARCHITECTURE_README = """# Architecture notes

Level-2 (guided) authority. Agents should follow these unless overridden with
a reason. Unlike contracts, these are narrative rather than rule lists.

Typical contents:

- system overview
- subsystem boundaries
- protocol and data-flow descriptions
- ADRs (architecture decision records)
"""

ARCHITECTURE_EXAMPLE = """# Example architecture note

Delete this file once you have real architecture notes.

## Overview

Describe the high-level architecture here. `repoctx` will surface this note
in bundles when a task touches files the note governs.
"""

EXAMPLES_README = """# Examples

Validating examples for contracts and architecture notes. `repoctx` treats
files here as Level-2 authority. Examples should be runnable or otherwise
demonstrable — prefer small, focused files that showcase a single rule.
"""


@dataclass(slots=True)
class ScaffoldResult:
    created: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "created": [str(p) for p in self.created],
            "skipped": [str(p) for p in self.skipped],
        }


FILES: tuple[tuple[str, str], ...] = (
    ("contracts/README.md", CONTRACTS_README),
    ("contracts/example.md", CONTRACTS_EXAMPLE),
    ("docs/architecture/README.md", ARCHITECTURE_README),
    ("docs/architecture/example.md", ARCHITECTURE_EXAMPLE),
    ("examples/README.md", EXAMPLES_README),
)


def init_authority(repo_root: str | Path = ".") -> ScaffoldResult:
    root = Path(repo_root).resolve()
    result = ScaffoldResult()
    for rel_path, content in FILES:
        path = root / rel_path
        if path.exists():
            result.skipped.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        result.created.append(path)
    return result


__all__ = ["ScaffoldResult", "init_authority"]
