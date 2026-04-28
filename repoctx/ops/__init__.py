"""Direct utility ops that expose RepoCtx subsystems to agents.

Distinct from :mod:`repoctx.protocol` (task-shaped flows: bundle, scope, etc.).
Ops here are thin wrappers over indexed data — agents call them when they
want to do their own retrieval rather than receive a task-shaped bundle.
"""

from repoctx.ops.semantic_search import op_semantic_search

__all__ = ["op_semantic_search"]
