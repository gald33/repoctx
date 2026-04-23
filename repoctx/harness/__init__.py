"""Harness-specific adapters for repoctx v2.

The core is environment-agnostic; these modules translate between repoctx's
protocol and the specific conventions of each coding-agent harness.
"""

from repoctx.harness.claude_code import (
    AGENTS_SECTION_HEADER,
    install_claude_code,
    render_agents_section,
)
from repoctx.harness.codex import install_codex
from repoctx.harness.cursor import install_cursor

__all__ = [
    "AGENTS_SECTION_HEADER",
    "install_claude_code",
    "install_codex",
    "install_cursor",
    "render_agents_section",
]
