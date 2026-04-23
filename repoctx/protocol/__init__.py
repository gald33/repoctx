"""Protocol operations exposed by repoctx v2.

Six operations form the token-aware protocol (see design doc § 4):

- bundle(task)
- authority(task, include?)
- scope(task)
- validate_plan(task, changed_files)
- risk_report(task, changed_files)
- refresh(task, changed_files, current_scope)
"""

from repoctx.protocol.bundle_op import op_bundle
from repoctx.protocol.authority_op import op_authority
from repoctx.protocol.scope_op import op_scope
from repoctx.protocol.validate_op import op_validate_plan
from repoctx.protocol.risk_op import op_risk_report
from repoctx.protocol.refresh_op import op_refresh

__all__ = [
    "op_authority",
    "op_bundle",
    "op_refresh",
    "op_risk_report",
    "op_scope",
    "op_validate_plan",
]
