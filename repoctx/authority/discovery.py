"""Authority discovery: convert a repository index into authority records.

This producer reuses :func:`repoctx.scanner.scan_repository` output (a
``RepositoryIndex``) rather than walking the tree itself, so authority
discovery piggy-backs on the existing single-pass scan.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from repoctx.authority.markers import Marker, parse_markers
from repoctx.authority.records import (
    AUTHORITY_NAMESPACE,
    AuthorityLevel,
    AuthorityRecord,
    AuthorityType,
    authority_record_to_retrievable,
)
from repoctx.config import DEFAULT_CONFIG, RepoCtxConfig
from repoctx.models import FileRecord, RepositoryIndex
from repoctx.record import RetrievableRecord
from repoctx.scanner import scan_repository

# Convention-based discovery. These globs are matched against the POSIX
# relative path of each file in the repository index.
HARD_AUTHORITY_GLOBS: dict[AuthorityType, tuple[str, ...]] = {
    "contract":         ("contracts/**", "contract/**", "**/*.contract.md"),
    "invariant":        ("docs/invariants/**", "**/*.invariant.md"),
    "validation_rule":  ("tests/contracts/**", "tests/invariants/**"),
}

GUIDED_AUTHORITY_GLOBS: dict[AuthorityType, tuple[str, ...]] = {
    "agent_instruction":   ("AGENTS.md", "AGENT.md", "CLAUDE.md", ".cursor/rules/**"),
    "architecture_note":   ("docs/architecture/**", "docs/adr/**", "adr/**"),
    "example":             ("examples/**", "example/**"),
}


def _match_any(path: str, globs: tuple[str, ...]) -> bool:
    posix = PurePosixPath(path).as_posix()
    return any(fnmatch.fnmatch(posix, g) for g in globs)


def _classify_path(path: str) -> tuple[AuthorityType, AuthorityLevel] | None:
    for atype, globs in HARD_AUTHORITY_GLOBS.items():
        if _match_any(path, globs):
            return atype, AuthorityLevel.HARD
    for atype, globs in GUIDED_AUTHORITY_GLOBS.items():
        if _match_any(path, globs):
            return atype, AuthorityLevel.GUIDED
    return None


def _title_from_content(record: FileRecord) -> str:
    for line in record.content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or record.name
        if stripped:
            return stripped[:120]
    return record.name


def _summary_from_content(record: FileRecord, max_chars: int = 240) -> str:
    for line in record.content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped[:max_chars]
    return record.name


@dataclass(slots=True)
class AuthorityProducer:
    """Produce authority records (also usable as retrievable records) from a repo."""

    repo_root: Path
    config: RepoCtxConfig = DEFAULT_CONFIG
    _index: RepositoryIndex | None = field(default=None, init=False, repr=False)

    def scan(self) -> RepositoryIndex:
        if self._index is None:
            self._index = scan_repository(self.repo_root, config=self.config)
        return self._index

    def build_authority_records(self) -> list[AuthorityRecord]:
        index = self.scan()
        records: list[AuthorityRecord] = []

        # 1. Convention-based whole-file authority records.
        from repoctx.authority.extract import parse_front_matter

        classified_ids: set[str] = set()
        for file_record in index.records.values():
            classification = _classify_path(file_record.path)
            if classification is None:
                continue
            atype, level = classification
            rec_id = f"{atype}:{file_record.path}"
            applies_to: list[str] = []
            if atype in ("contract", "invariant") and file_record.content:
                fm, _ = parse_front_matter(file_record.content)
                raw = fm.get("applies_to")
                if isinstance(raw, list):
                    applies_to = [str(v).strip().strip("\"'") for v in raw if str(v).strip()]
                elif isinstance(raw, str) and raw.strip():
                    applies_to = [raw.strip().strip("\"'")]
            records.append(
                AuthorityRecord(
                    id=rec_id,
                    type=atype,
                    path=file_record.path,
                    title=_title_from_content(file_record),
                    summary=_summary_from_content(file_record),
                    text=file_record.content[:8000],
                    authority_level=level,
                    tags=[atype],
                    applies_to_paths=applies_to,
                )
            )
            classified_ids.add(rec_id)

        # 2. Inline-marker authority records.
        for file_record in index.records.values():
            if not file_record.content:
                continue
            markers = parse_markers(file_record.content)
            for m in markers:
                records.append(_record_from_marker(file_record, m))

        return records

    # ---- RecordProducer protocol --------------------------------------------------

    def build_records(self) -> list[RetrievableRecord]:
        return [authority_record_to_retrievable(r) for r in self.build_authority_records()]


def _record_from_marker(file_record: FileRecord, marker: Marker) -> AuthorityRecord:
    type_map: dict[str, tuple[AuthorityType, AuthorityLevel]] = {
        "invariant":    ("invariant", AuthorityLevel.HARD),
        "do_not":       ("invariant", AuthorityLevel.HARD),
        "contract":     ("contract",  AuthorityLevel.HARD),
        "important":    ("architecture_note", AuthorityLevel.GUIDED),
        "see_contract": ("architecture_note", AuthorityLevel.GUIDED),
    }
    atype, level = type_map[marker.kind]
    rec_id = f"{atype}:{file_record.path}#L{marker.line}"
    title_prefix = {
        "invariant": "INVARIANT",
        "do_not": "DO NOT",
        "contract": "CONTRACT",
        "important": "IMPORTANT",
        "see_contract": "See contract",
    }[marker.kind]
    title = f"{title_prefix}: {marker.body[:100]}"
    return AuthorityRecord(
        id=rec_id,
        type=atype,
        path=f"{file_record.path}:{marker.line}",
        title=title,
        summary=marker.body[:240],
        text=marker.body,
        authority_level=level,
        tags=[atype, "inline"],
        applies_to_paths=[file_record.path],
    )


__all__ = ["AuthorityProducer", "AUTHORITY_NAMESPACE"]
