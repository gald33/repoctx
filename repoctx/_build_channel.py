"""Release-channel constants baked into the build at packaging time.

This file is the single source of truth for which channel a given install
belongs to. The release pipeline overwrites it for canary builds:

  - Stable wheels (``pip install repoctx-mcp``): CHANNEL = "stable"
  - Canary wheels (``pip install --pre repoctx-mcp``, version "1.x.x.devN"):
    CHANNEL = "canary"

The default committed to source is ``"stable"`` so a checkout-built install
behaves like a stable build unless the canary pipeline rewrites this file
before ``python -m build``. ``BUILD_ID`` is informational — combined with
``CHANNEL`` it lets the reporting endpoint distinguish which exact wheel
emitted an event without needing per-build secrets or signed manifests.
"""

from __future__ import annotations

from typing import Literal

Channel = Literal["stable", "canary"]

CHANNEL: Channel = "stable"

# Free-form identifier, typically "<version>+<channel>.<date>.<short_sha>".
# Set by the release pipeline; defaults to the package version at import
# time so dev checkouts don't crash on missing build metadata.
BUILD_ID: str = "dev"
