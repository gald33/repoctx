"""Inline marker parsing for authority discovery.

Supported markers (language-agnostic — we match on line text after stripping
common comment prefixes ``#``, ``//``, ``--``, ``;``, ``*``):

- ``INVARIANT: <rule>``     → Level 1 constraint statement, scoped to file
- ``CONTRACT: <rule>``      → Level 1 constraint statement
- ``DO NOT: <rule>``        → Level 1 constraint statement (hard)
- ``IMPORTANT: <note>``     → Level 2 agent-facing note
- ``See contract: <path>``  → cross-reference edge to an authority record
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

MarkerKind = Literal["invariant", "contract", "do_not", "important", "see_contract"]

_COMMENT_PREFIX_RE = re.compile(r"^\s*(?://+|#+|--+|;+|\*+|<!--)\s*")
_MARKER_RE = re.compile(
    r"^(?P<kind>INVARIANT|CONTRACT|DO NOT|IMPORTANT|See contract)\s*:\s*(?P<body>.+?)\s*(?:-->)?\s*$",
    re.IGNORECASE,
)

_KIND_MAP: dict[str, MarkerKind] = {
    "invariant": "invariant",
    "contract": "contract",
    "do not": "do_not",
    "important": "important",
    "see contract": "see_contract",
}


@dataclass(slots=True)
class Marker:
    kind: MarkerKind
    body: str
    line: int  # 1-indexed


def parse_markers(text: str) -> list[Marker]:
    """Return all authority markers found in ``text``, in source order."""
    markers: list[Marker] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped = _COMMENT_PREFIX_RE.sub("", raw).strip()
        if not stripped:
            continue
        m = _MARKER_RE.match(stripped)
        if not m:
            continue
        kind_key = m.group("kind").lower()
        kind = _KIND_MAP.get(kind_key)
        if kind is None:
            continue
        body = m.group("body").strip()
        if not body:
            continue
        markers.append(Marker(kind=kind, body=body, line=lineno))
    return markers
