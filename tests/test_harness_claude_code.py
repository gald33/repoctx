"""Tests for the Claude Code harness installer (idempotent AGENTS + .mcp.json)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dataclasses import replace

from repoctx.config import DEFAULT_CONFIG
from repoctx.harness import (
    AGENTS_SECTION_HEADER,
    ensure_claude_md_nudge,
    install_claude_code,
)
from repoctx.harness.claude_code import (
    ACTION_NO_OP,
    ACTION_NUDGE_INSERTED,
    ACTION_POINTER_CREATED,
    ACTION_SKIPPED,
    ENV_DISABLE_CLAUDE_MD_NUDGE,
    NUDGE_BLOCK,
    NUDGE_MARKER,
    POINTER_MARKER,
    _classify_md,
)

# Backward-compat aliases used by older imports/tests.
CLAUDE_MD_NUDGE_MARKER = NUDGE_MARKER
CLAUDE_MD_NUDGE_BLOCK = NUDGE_BLOCK


def test_installer_creates_agents_md_and_mcp_config(tmp_path: Path) -> None:
    result = install_claude_code(tmp_path)
    assert result.agents_md_changed
    assert result.mcp_config_changed
    agents = (tmp_path / "AGENTS.md").read_text()
    assert AGENTS_SECTION_HEADER in agents
    assert "repoctx.bundle(task)" in agents
    mcp = json.loads((tmp_path / ".mcp.json").read_text())
    assert "repoctx" in mcp["mcpServers"]


def test_installer_is_idempotent(tmp_path: Path) -> None:
    install_claude_code(tmp_path)
    second = install_claude_code(tmp_path)
    assert not second.agents_md_changed
    assert not second.mcp_config_changed


def test_installer_appends_to_existing_agents_md(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# Agents\n\nAlready has content.\n")
    install_claude_code(tmp_path)
    text = (tmp_path / "AGENTS.md").read_text()
    assert "Already has content." in text
    assert AGENTS_SECTION_HEADER in text


def test_installer_preserves_other_mcp_servers(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"other": {"command": "foo"}}})
    )
    install_claude_code(tmp_path)
    mcp = json.loads((tmp_path / ".mcp.json").read_text())
    assert "other" in mcp["mcpServers"]
    assert "repoctx" in mcp["mcpServers"]


def test_installer_writes_post_tool_hook(tmp_path: Path) -> None:
    result = install_claude_code(tmp_path)
    assert result.settings_changed
    assert result.settings_path is not None
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    matchers = settings["hooks"]["PostToolUse"]
    assert any(
        any(h.get("command", "").startswith("repoctx update") for h in (m.get("hooks") or []))
        for m in matchers
    )


def test_installer_hook_is_idempotent(tmp_path: Path) -> None:
    install_claude_code(tmp_path)
    second = install_claude_code(tmp_path)
    assert not second.settings_changed
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    matchers = settings["hooks"]["PostToolUse"]
    repoctx_hooks = [
        h
        for m in matchers
        for h in (m.get("hooks") or [])
        if h.get("command", "").startswith("repoctx update")
    ]
    assert len(repoctx_hooks) == 1


def test_installer_preserves_existing_settings(tmp_path: Path) -> None:
    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    (settings_dir / "settings.json").write_text(
        json.dumps({"hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo"}]}]}})
    )
    install_claude_code(tmp_path)
    settings = json.loads((settings_dir / "settings.json").read_text())
    matchers = settings["hooks"]["PostToolUse"]
    commands = [
        h.get("command")
        for m in matchers
        for h in (m.get("hooks") or [])
    ]
    assert "echo" in commands
    assert any(c and c.startswith("repoctx update") for c in commands)


def test_installer_agents_section_includes_upkeep(tmp_path: Path) -> None:
    install_claude_code(tmp_path)
    text = (tmp_path / "AGENTS.md").read_text()
    assert "Embedding upkeep" in text
    assert "repoctx update" in text


# -- _classify_md --------------------------------------------------------------


def test_classify_md_absent(tmp_path: Path) -> None:
    assert _classify_md(tmp_path / "CLAUDE.md", "AGENTS.md") == "absent"


def test_classify_md_pointer_via_marker(tmp_path: Path) -> None:
    """Files we created carry POINTER_MARKER and are always classified as pointer."""
    path = tmp_path / "CLAUDE.md"
    path.write_text(f"{POINTER_MARKER}\n@AGENTS.md\n")
    assert _classify_md(path, "AGENTS.md") == "pointer"


def test_classify_md_pointer_via_heuristic(tmp_path: Path) -> None:
    """Short hand-written pointer (title + import line) → pointer."""
    path = tmp_path / "CLAUDE.md"
    path.write_text("# Project\n\n@AGENTS.md\n")
    assert _classify_md(path, "AGENTS.md") == "pointer"


def test_classify_md_pointer_just_import(tmp_path: Path) -> None:
    """Bare `@AGENTS.md` import → pointer."""
    path = tmp_path / "CLAUDE.md"
    path.write_text("@AGENTS.md\n")
    assert _classify_md(path, "AGENTS.md") == "pointer"


def test_classify_md_content_when_substantive(tmp_path: Path) -> None:
    """Short file with import + meaningful note → content (user took ownership)."""
    path = tmp_path / "CLAUDE.md"
    path.write_text(
        "# Project\n\n@AGENTS.md\n\nClaude-specific note: use bun, not npm.\n"
    )
    assert _classify_md(path, "AGENTS.md") == "content"


def test_classify_md_content_when_long(tmp_path: Path) -> None:
    """File over the byte threshold → content even if it has an import."""
    path = tmp_path / "CLAUDE.md"
    path.write_text("@AGENTS.md\n" + "# Heading\n\nBody text. " * 200)
    assert _classify_md(path, "AGENTS.md") == "content"


def test_classify_md_content_no_import(tmp_path: Path) -> None:
    """Short file without an import directive → content."""
    path = tmp_path / "CLAUDE.md"
    path.write_text("# Project\n\nSome notes.\n")
    assert _classify_md(path, "AGENTS.md") == "content"


# -- _classify_md: pointer_max_substantive_lines knob -------------------------


def test_classify_md_threshold_raised_treats_title_plus_one_note_as_pointer(
    tmp_path: Path,
) -> None:
    """The edge case from the v1.2.0 follow-up: a hand-written CLAUDE.md with
    a title + ``@AGENTS.md`` import + one Claude-specific note has 2
    substantive lines, which the default threshold (1) classifies as
    ``content`` so the nudge block lands there. Raising the threshold to 2
    flips it back to ``pointer`` so the nudge stays in AGENTS.md only.
    """
    path = tmp_path / "CLAUDE.md"
    path.write_text(
        "# Project\n\n@AGENTS.md\n\nClaude-specific note: use bun, not npm.\n"
    )
    # Default → content (existing behavior).
    assert _classify_md(path, "AGENTS.md") == "content"
    # Threshold=2 → pointer.
    cfg = replace(DEFAULT_CONFIG, pointer_max_substantive_lines=2)
    assert _classify_md(path, "AGENTS.md", config=cfg) == "pointer"


def test_classify_md_threshold_zero_treats_bare_import_as_pointer(
    tmp_path: Path,
) -> None:
    """A bare ``@OTHER.md`` import has 0 substantive lines, so it stays a
    pointer even with the threshold tightened to 0. This pins the lower
    bound: the import-only file is never reclassified as content.
    """
    path = tmp_path / "CLAUDE.md"
    path.write_text("@AGENTS.md\n")
    cfg = replace(DEFAULT_CONFIG, pointer_max_substantive_lines=0)
    assert _classify_md(path, "AGENTS.md", config=cfg) == "pointer"


def test_classify_md_threshold_zero_demotes_title_plus_import_to_content(
    tmp_path: Path,
) -> None:
    """Threshold=0 means *any* substantive line (including a title) flips
    the file to ``content``. Useful for users who want repoctx to never
    treat a hand-written CLAUDE.md as a pointer.
    """
    path = tmp_path / "CLAUDE.md"
    path.write_text("# Project\n\n@AGENTS.md\n")
    cfg = replace(DEFAULT_CONFIG, pointer_max_substantive_lines=0)
    assert _classify_md(path, "AGENTS.md", config=cfg) == "content"


def test_ensure_nudge_threshold_keeps_curated_claude_md_as_pointer(
    tmp_path: Path,
) -> None:
    """End-to-end: with threshold=2, a curated CLAUDE.md (title + import +
    one note) is treated as a pointer, so the nudge block lands in
    AGENTS.md only and CLAUDE.md is left byte-identical.
    """
    (tmp_path / "CLAUDE.md").write_text(
        "# Project\n\n@AGENTS.md\n\nUse bun, not npm.\n"
    )
    (tmp_path / "AGENTS.md").write_text("# Agents\n\nProject conventions.\n")
    claude_snapshot = (tmp_path / "CLAUDE.md").read_bytes()
    cfg = replace(DEFAULT_CONFIG, pointer_max_substantive_lines=2)
    result = ensure_claude_md_nudge(tmp_path, config=cfg)
    assert result.claude_md_action == ACTION_NO_OP
    assert result.agents_md_action == ACTION_NUDGE_INSERTED
    assert (tmp_path / "CLAUDE.md").read_bytes() == claude_snapshot
    assert NUDGE_MARKER in (tmp_path / "AGENTS.md").read_text()


# -- ensure_claude_md_nudge: dispatch matrix -----------------------------------


def test_nudge_creates_pointer_when_claude_md_absent_and_agents_has_content(
    tmp_path: Path,
) -> None:
    """No CLAUDE.md + AGENTS has content → pointer created, nudge in AGENTS."""
    (tmp_path / "AGENTS.md").write_text(
        "# Agents\n\n## Project guidance\n\nUse pytest.\n"
    )
    result = ensure_claude_md_nudge(tmp_path)
    assert result.claude_md_action == ACTION_POINTER_CREATED
    assert result.agents_md_action == ACTION_NUDGE_INSERTED
    claude_text = (tmp_path / "CLAUDE.md").read_text()
    assert POINTER_MARKER in claude_text
    assert "@AGENTS.md" in claude_text
    assert NUDGE_MARKER not in claude_text  # nudge is in AGENTS, not the pointer
    assert NUDGE_MARKER in (tmp_path / "AGENTS.md").read_text()


def test_nudge_skips_when_both_files_absent(tmp_path: Path) -> None:
    """No AGENTS.md, no CLAUDE.md → both skipped, nothing created."""
    result = ensure_claude_md_nudge(tmp_path)
    assert result.claude_md_action == ACTION_SKIPPED
    assert result.agents_md_action == ACTION_SKIPPED
    assert not (tmp_path / "CLAUDE.md").exists()
    assert not (tmp_path / "AGENTS.md").exists()


def test_nudge_pointer_claude_md_lands_in_agents_only(tmp_path: Path) -> None:
    """CLAUDE.md is a pointer → nudge in AGENTS only; CLAUDE.md untouched."""
    (tmp_path / "CLAUDE.md").write_text(f"{POINTER_MARKER}\n@AGENTS.md\n")
    (tmp_path / "AGENTS.md").write_text("# Agents\n\n## Section\nBody.\n")
    snapshot = (tmp_path / "CLAUDE.md").read_bytes()
    result = ensure_claude_md_nudge(tmp_path)
    assert result.claude_md_action == ACTION_NO_OP
    assert result.agents_md_action == ACTION_NUDGE_INSERTED
    assert (tmp_path / "CLAUDE.md").read_bytes() == snapshot
    assert NUDGE_MARKER in (tmp_path / "AGENTS.md").read_text()


def test_nudge_both_content_lands_in_both(tmp_path: Path) -> None:
    """Both files have substantive content → nudge in both."""
    (tmp_path / "CLAUDE.md").write_text("# Project\n\nClaude-specific guidance.\n")
    (tmp_path / "AGENTS.md").write_text("# Agents\n\nProject conventions.\n")
    result = ensure_claude_md_nudge(tmp_path)
    assert result.claude_md_action == ACTION_NUDGE_INSERTED
    assert result.agents_md_action == ACTION_NUDGE_INSERTED
    assert NUDGE_MARKER in (tmp_path / "CLAUDE.md").read_text()
    assert NUDGE_MARKER in (tmp_path / "AGENTS.md").read_text()


def test_nudge_content_claude_pointer_agents_lands_in_claude_only(
    tmp_path: Path,
) -> None:
    """CLAUDE has content, AGENTS is a pointer → nudge in CLAUDE only."""
    (tmp_path / "CLAUDE.md").write_text("# Project\n\nReal content here.\n")
    (tmp_path / "AGENTS.md").write_text(f"{POINTER_MARKER}\n@CLAUDE.md\n")
    agents_snapshot = (tmp_path / "AGENTS.md").read_bytes()
    result = ensure_claude_md_nudge(tmp_path)
    assert result.claude_md_action == ACTION_NUDGE_INSERTED
    assert result.agents_md_action == ACTION_NO_OP
    assert NUDGE_MARKER in (tmp_path / "CLAUDE.md").read_text()
    assert (tmp_path / "AGENTS.md").read_bytes() == agents_snapshot


def test_nudge_content_claude_absent_agents_lands_in_claude_only(
    tmp_path: Path,
) -> None:
    """CLAUDE has content, AGENTS is absent → nudge in CLAUDE only."""
    (tmp_path / "CLAUDE.md").write_text("# Project\n\nContent.\n")
    result = ensure_claude_md_nudge(tmp_path)
    assert result.claude_md_action == ACTION_NUDGE_INSERTED
    assert result.agents_md_action == ACTION_SKIPPED
    assert NUDGE_MARKER in (tmp_path / "CLAUDE.md").read_text()
    assert not (tmp_path / "AGENTS.md").exists()


# -- ensure_claude_md_nudge: write semantics -----------------------------------


def test_nudge_appends_when_no_separator(tmp_path: Path) -> None:
    """File without a `---` line → block appended at EOF with blank-line gap."""
    (tmp_path / "CLAUDE.md").write_text("# Project\n\nSome guidance.\n")
    result = ensure_claude_md_nudge(tmp_path)
    assert result.claude_md_action == ACTION_NUDGE_INSERTED
    text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Some guidance." in text
    assert text.endswith(NUDGE_BLOCK)
    assert "\n\n" + NUDGE_MARKER in text


def test_nudge_inserts_before_first_separator(tmp_path: Path) -> None:
    """File with a `---` line → block inserted immediately before it."""
    original = "# Project\nIntro paragraph.\n---\n## Section\nBody.\n"
    (tmp_path / "CLAUDE.md").write_text(original)
    result = ensure_claude_md_nudge(tmp_path)
    assert result.claude_md_action == ACTION_NUDGE_INSERTED
    text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    marker_idx = text.index(NUDGE_MARKER)
    sep_idx = text.index("\n---\n")
    assert marker_idx < sep_idx
    assert "Intro paragraph." in text[:marker_idx]
    assert "## Section" in text[sep_idx:]


def test_nudge_is_idempotent(tmp_path: Path) -> None:
    """Marker already present → no-op, file byte-identical."""
    (tmp_path / "CLAUDE.md").write_text("# Project\n\nBody.\n")
    ensure_claude_md_nudge(tmp_path)
    snapshot = (tmp_path / "CLAUDE.md").read_bytes()
    result = ensure_claude_md_nudge(tmp_path)
    assert result.claude_md_action == ACTION_NO_OP
    assert (tmp_path / "CLAUDE.md").read_bytes() == snapshot


def test_nudge_self_heals_after_marker_deletion(tmp_path: Path) -> None:
    """Delete the block, re-run → block is re-added in canonical form."""
    (tmp_path / "CLAUDE.md").write_text("# Project\n\nBody.\n")
    ensure_claude_md_nudge(tmp_path)
    canonical = (tmp_path / "CLAUDE.md").read_bytes()
    (tmp_path / "CLAUDE.md").write_text("# Project\n\nBody.\n")
    result = ensure_claude_md_nudge(tmp_path)
    assert result.claude_md_action == ACTION_NUDGE_INSERTED
    assert (tmp_path / "CLAUDE.md").read_bytes() == canonical


def test_nudge_pointer_creation_idempotent_on_second_run(tmp_path: Path) -> None:
    """After pointer creation, a second run is a no-op (CLAUDE is now pointer)."""
    (tmp_path / "AGENTS.md").write_text("# Agents\n\nReal content.\n")
    first = ensure_claude_md_nudge(tmp_path)
    assert first.claude_md_action == ACTION_POINTER_CREATED
    claude_snapshot = (tmp_path / "CLAUDE.md").read_bytes()
    agents_snapshot = (tmp_path / "AGENTS.md").read_bytes()
    second = ensure_claude_md_nudge(tmp_path)
    assert second.claude_md_action == ACTION_NO_OP
    assert second.agents_md_action == ACTION_NO_OP
    assert (tmp_path / "CLAUDE.md").read_bytes() == claude_snapshot
    assert (tmp_path / "AGENTS.md").read_bytes() == agents_snapshot


def test_nudge_disabled_via_parameter(tmp_path: Path) -> None:
    """enabled=False → both files skipped, no writes."""
    (tmp_path / "CLAUDE.md").write_text("# Project\n")
    (tmp_path / "AGENTS.md").write_text("# Agents\n\nContent.\n")
    result = ensure_claude_md_nudge(tmp_path, enabled=False)
    assert result.claude_md_action == ACTION_SKIPPED
    assert result.agents_md_action == ACTION_SKIPPED
    assert NUDGE_MARKER not in (tmp_path / "CLAUDE.md").read_text()
    assert NUDGE_MARKER not in (tmp_path / "AGENTS.md").read_text()


def test_nudge_disabled_via_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """REPOCTX_NO_CLAUDE_MD_NUDGE truthy → both files skipped."""
    (tmp_path / "CLAUDE.md").write_text("# Project\n")
    (tmp_path / "AGENTS.md").write_text("# Agents\n\nContent.\n")
    monkeypatch.setenv(ENV_DISABLE_CLAUDE_MD_NUDGE, "1")
    result = ensure_claude_md_nudge(tmp_path)
    assert result.claude_md_action == ACTION_SKIPPED
    assert result.agents_md_action == ACTION_SKIPPED
    assert NUDGE_MARKER not in (tmp_path / "CLAUDE.md").read_text()
    assert NUDGE_MARKER not in (tmp_path / "AGENTS.md").read_text()


# -- install_claude_code integration -------------------------------------------


def test_install_inserts_nudge_in_both_when_claude_md_has_content(
    tmp_path: Path,
) -> None:
    """Pre-existing content CLAUDE.md → install adds nudge there AND in AGENTS.md."""
    (tmp_path / "CLAUDE.md").write_text("# Project\n\nContent.\n")
    result = install_claude_code(tmp_path)
    assert result.claude_md == (tmp_path / "CLAUDE.md").resolve()
    assert result.claude_md_action == ACTION_NUDGE_INSERTED
    assert result.claude_md_changed is True
    assert result.agents_md_nudge_changed is True
    assert NUDGE_MARKER in (tmp_path / "CLAUDE.md").read_text()
    assert NUDGE_MARKER in (tmp_path / "AGENTS.md").read_text()


def test_install_creates_pointer_when_claude_md_missing(tmp_path: Path) -> None:
    """No CLAUDE.md → install creates AGENTS.md (with section), then a pointer
    CLAUDE.md, then puts the nudge in AGENTS.md."""
    result = install_claude_code(tmp_path)
    assert result.claude_md_action == ACTION_POINTER_CREATED
    assert result.claude_md_changed is True
    claude_text = (tmp_path / "CLAUDE.md").read_text()
    assert POINTER_MARKER in claude_text
    assert "@AGENTS.md" in claude_text
    # Nudge lives in AGENTS.md (alongside the existing Ground truth section).
    assert result.agents_md_nudge_changed is True
    assert NUDGE_MARKER in (tmp_path / "AGENTS.md").read_text()


def test_install_opt_out_omits_nudge_keys(tmp_path: Path) -> None:
    """--no-claude-md-nudge → no pointer created, no block inserted, JSON omits keys."""
    (tmp_path / "CLAUDE.md").write_text("# Project\n")
    result = install_claude_code(tmp_path, claude_md_nudge=False)
    assert result.claude_md is None
    assert result.claude_md_changed is False
    assert result.claude_md_action is None
    assert result.agents_md_nudge_changed is False
    payload = result.to_dict()
    assert "claude_md" not in payload
    assert "claude_md_action" not in payload
    assert "agents_md_nudge_changed" not in payload
    assert NUDGE_MARKER not in (tmp_path / "CLAUDE.md").read_text()


def test_install_to_dict_includes_claude_md_action(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("# Project\n\nContent.\n")
    result = install_claude_code(tmp_path)
    payload = result.to_dict()
    assert payload["claude_md_action"] == ACTION_NUDGE_INSERTED
    assert payload["agents_md_nudge_changed"] is True


# -- v2 anchor block (stronger directive + non-trivial definition) ----------


def test_install_writes_v2_anchor_block(tmp_path: Path) -> None:
    """New installs ship the v2 marker with the "you must call" wording."""
    (tmp_path / "CLAUDE.md").write_text("# Project\n\nContent.\n")
    install_claude_code(tmp_path)
    text = (tmp_path / "CLAUDE.md").read_text()
    assert "<!-- repoctx-nudge:v2 -->" in text
    assert "**must call**" in text
    assert "Non-trivial =" in text


def test_install_upgrades_v1_anchor_block_in_place(tmp_path: Path) -> None:
    """v1 marker present → block is rewritten in place to v2, surroundings preserved."""
    from repoctx.harness.claude_code import NUDGE_MARKER_V1

    before = (
        "# Project\n\n"
        "Some intro line.\n\n"
        f"{NUDGE_MARKER_V1}\n"
        "> **repoctx is installed for this repo.** For non-trivial tasks, call\n"
        "> `mcp__repoctx__bundle(task)` before proposing a plan, and\n"
        "> `mcp__repoctx__validate_plan` + `mcp__repoctx__risk_report` before\n"
        "> declaring done.\n\n"
        "## Later section\n\nDon't touch me.\n"
    )
    (tmp_path / "CLAUDE.md").write_text(before)
    install_claude_code(tmp_path)
    after = (tmp_path / "CLAUDE.md").read_text()

    assert NUDGE_MARKER_V1 not in after
    assert "<!-- repoctx-nudge:v2 -->" in after
    assert "**must call**" in after
    assert "Some intro line." in after
    assert "Don't touch me." in after
    # Exactly one v2 marker — migration shouldn't have stacked blocks.
    assert after.count("<!-- repoctx-nudge:v2 -->") == 1


def test_v1_to_v2_migration_is_idempotent(tmp_path: Path) -> None:
    """A second install after the v1→v2 migration is a no-op."""
    from repoctx.harness.claude_code import NUDGE_MARKER_V1

    (tmp_path / "CLAUDE.md").write_text(
        f"# Project\n\n{NUDGE_MARKER_V1}\n> Old wording.\n"
    )
    first = install_claude_code(tmp_path)
    second = install_claude_code(tmp_path)
    assert first.claude_md_action == ACTION_NUDGE_INSERTED
    assert second.claude_md_action == ACTION_NO_OP


# -- UserPromptSubmit / Stop hooks ------------------------------------------


def _hook_commands(settings: dict, event: str) -> list[str]:
    return [
        h.get("command", "")
        for entry in settings.get("hooks", {}).get(event, [])
        if isinstance(entry, dict)
        for h in (entry.get("hooks") or [])
        if isinstance(h, dict)
    ]


def test_installer_writes_prompt_submit_and_stop_hooks(tmp_path: Path) -> None:
    install_claude_code(tmp_path)
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())

    prompt_cmds = _hook_commands(settings, "UserPromptSubmit")
    assert any(c.startswith("repoctx hook prompt-nudge") for c in prompt_cmds)

    stop_cmds = _hook_commands(settings, "Stop")
    assert any(c.startswith("repoctx hook stop-check") for c in stop_cmds)


def test_new_hooks_are_idempotent(tmp_path: Path) -> None:
    install_claude_code(tmp_path)
    second = install_claude_code(tmp_path)
    assert not second.settings_changed
    settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())

    for event, prefix in [
        ("PostToolUse", "repoctx update"),
        ("UserPromptSubmit", "repoctx hook prompt-nudge"),
        ("Stop", "repoctx hook stop-check"),
    ]:
        matching = [c for c in _hook_commands(settings, event) if c.startswith(prefix)]
        assert len(matching) == 1, (event, matching)


def test_new_hooks_preserve_user_authored_entries(tmp_path: Path) -> None:
    settings_dir = tmp_path / ".claude"
    settings_dir.mkdir()
    (settings_dir / "settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "UserPromptSubmit": [
                        {"hooks": [{"type": "command", "command": "echo prompt"}]}
                    ],
                    "Stop": [
                        {"hooks": [{"type": "command", "command": "echo stop"}]}
                    ],
                }
            }
        )
    )
    install_claude_code(tmp_path)
    settings = json.loads((settings_dir / "settings.json").read_text())

    prompt_cmds = _hook_commands(settings, "UserPromptSubmit")
    assert "echo prompt" in prompt_cmds
    assert any(c.startswith("repoctx hook prompt-nudge") for c in prompt_cmds)

    stop_cmds = _hook_commands(settings, "Stop")
    assert "echo stop" in stop_cmds
    assert any(c.startswith("repoctx hook stop-check") for c in stop_cmds)
