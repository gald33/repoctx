"""Richer constraint extraction from authority records.

Phase 3 (design doc § 2.4):

- YAML-ish front-matter parsing (simple subset: top-level scalars + ``- item`` lists).
- Bullet extraction under ``## Invariants`` / ``## Contracts`` / ``## Do not``
  / ``## Do Not`` headings in markdown authority records.
- Inline markers promoted to constraints (already modeled as AuthorityRecord).

Each returned :class:`Constraint` has a stable id derived from source +
statement (see :func:`repoctx.authority.constraints.constraint_id`).
"""

from __future__ import annotations

import re
from typing import Iterable

from repoctx.authority.constraints import Constraint, Scope, Severity, constraint_id
from repoctx.authority.records import AuthorityLevel, AuthorityRecord

_FRONT_MATTER_RE = re.compile(r"\A---\s*\n(?P<body>.*?)\n---\s*\n", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{2,4})\s+(?P<title>.+?)\s*$")
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+(?P<item>.+?)\s*$")

_CONSTRAINT_HEADINGS = {
    "invariants", "invariant",
    "contracts", "contract",
    "do not", "do-not", "donot",
    "rules",
}


def parse_front_matter(text: str) -> tuple[dict[str, object], str]:
    """Parse a YAML-ish front-matter block. Returns (mapping, body_without_block).

    Supports the pragmatic subset we need:

    ``key: value``           → string scalar
    ``key:``                 → list, whose items follow as ``- item`` lines
    ``  - item``             → list item (indentation ignored)
    """
    m = _FRONT_MATTER_RE.match(text)
    if not m:
        return {}, text
    raw = m.group("body")
    rest = text[m.end():]
    data: dict[str, object] = {}
    current_key: str | None = None
    for raw_line in raw.splitlines():
        if not raw_line.strip():
            continue
        stripped = raw_line.rstrip()
        if stripped.lstrip().startswith("-") and current_key is not None:
            item = stripped.lstrip()[1:].strip()
            bucket = data.setdefault(current_key, [])
            if isinstance(bucket, list):
                bucket.append(item)
            continue
        if ":" in stripped:
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            if not value:
                data[key] = []
                current_key = key
            else:
                data[key] = value
                current_key = None
    return data, rest


def extract_heading_bullets(text: str) -> list[tuple[str, list[str]]]:
    """Return ``[(heading_title_lower, [bullet_items])]`` for constraint headings.

    Soft-wrapped bullets are joined: an indented, non-empty line that is not a
    new bullet or heading is appended to the previous bullet's text. This
    matches common markdown style where long rules wrap across lines.
    """
    sections: list[tuple[str, list[str]]] = []
    current: tuple[str, list[str]] | None = None

    for line in text.splitlines():
        heading = _HEADING_RE.match(line)
        if heading:
            title = heading.group("title").strip().lower().rstrip(":")
            if title in _CONSTRAINT_HEADINGS:
                if current is not None:
                    sections.append(current)
                current = (title, [])
            else:
                if current is not None:
                    sections.append(current)
                    current = None
            continue
        if current is None:
            continue
        bullet = _BULLET_RE.match(line)
        if bullet:
            item = bullet.group("item").strip()
            if item:
                current[1].append(item)
            continue
        if line.startswith((" ", "\t")) and line.strip() and current[1]:
            current[1][-1] = f"{current[1][-1]} {line.strip()}"

    if current is not None:
        sections.append(current)
    return sections


def extract_constraints(records: Iterable[AuthorityRecord]) -> list[Constraint]:
    """Extract :class:`Constraint` objects from authority records.

    Phase-3 rules:

    1. Whole-file contract/invariant records — use front-matter (if present)
       to fill ``applies_to``, ``severity``, ``validated_by``. Each bullet
       under ``## Invariants|Contracts|Do not`` becomes one constraint.
       If no bullets, emit one constraint from the record summary.
    2. Inline-marker records (``tags`` contains ``"inline"``) → one constraint
       scoped to the marker's source file.
    """
    out: list[Constraint] = []
    seen: set[str] = set()

    def _push(c: Constraint) -> None:
        if c.id in seen:
            return
        seen.add(c.id)
        out.append(c)

    for record in records:
        if "inline" in record.tags:
            _push(_constraint_from_inline(record))
            continue
        if record.type not in ("contract", "invariant"):
            continue
        front_matter, body = parse_front_matter(record.text)
        applies_to = _as_str_list(front_matter.get("applies_to")) or list(record.applies_to_paths)
        severity: Severity = _normalize_severity(front_matter.get("severity"), record.authority_level)
        validated_by = _as_str_list(front_matter.get("validated_by"))
        validation_refs = [f"test:{p}" for p in validated_by]

        bullets: list[str] = []
        for _title, items in extract_heading_bullets(body):
            bullets.extend(items)

        if not bullets:
            bullets = [record.summary or record.title]

        scope: Scope = "subsystem" if applies_to else "global"
        for statement in bullets:
            statement = statement.strip()
            if not statement:
                continue
            _push(
                Constraint(
                    id=constraint_id(record.id, statement),
                    statement=statement,
                    source_record_id=record.id,
                    scope=scope,
                    applies_to_paths=list(applies_to),
                    severity=severity,
                    validation_refs=list(validation_refs),
                )
            )

    return out


# ---- helpers ----------------------------------------------------------------


def _constraint_from_inline(record: AuthorityRecord) -> Constraint:
    severity: Severity = "hard" if record.authority_level == AuthorityLevel.HARD else "guided"
    statement = record.summary or record.text or record.title
    return Constraint(
        id=constraint_id(record.id, statement),
        statement=statement,
        source_record_id=record.id,
        scope="path",
        applies_to_paths=list(record.applies_to_paths),
        severity=severity,
        validation_refs=[rid for rid in record.related_ids if rid.startswith("test:")],
    )


def _as_str_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip().strip("\"'") for v in value if str(v).strip()]
    if isinstance(value, str):
        return [value.strip().strip("\"'")] if value.strip() else []
    return []


def _normalize_severity(value: object, level: AuthorityLevel) -> Severity:
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("hard", "guided", "advisory"):
            return v  # type: ignore[return-value]
    return "hard" if level == AuthorityLevel.HARD else "guided"


__all__ = [
    "extract_constraints",
    "extract_heading_bullets",
    "parse_front_matter",
]
