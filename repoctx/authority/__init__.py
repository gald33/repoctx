"""Authority layer: typed records, constraints, and discovery for ground truth."""

from repoctx.authority.records import (
    AUTHORITY_RECORD_TYPES,
    AuthorityLevel,
    AuthorityRecord,
    authority_record_to_retrievable,
)
from repoctx.authority.constraints import Constraint
from repoctx.authority.discovery import AuthorityProducer

__all__ = [
    "AUTHORITY_RECORD_TYPES",
    "AuthorityLevel",
    "AuthorityRecord",
    "AuthorityProducer",
    "Constraint",
    "authority_record_to_retrievable",
]
