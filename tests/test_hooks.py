"""Unit tests for the Claude Code hook handlers (prompt-nudge, stop-check)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repoctx.hooks import (
    ENTRY_REMINDER,
    EXIT_REMINDER,
    SKIP_REASON_SUFFIX,
    count_turn_tool_uses,
    handle_prompt_submit,
    handle_stop,
)


# -- prompt-nudge -------------------------------------------------------------


def test_prompt_nudge_trivial_short_no_keyword_is_silent():
    out = handle_prompt_submit({"prompt": "hi"})
    assert out.stdout == ""
    assert out.stderr == ""


def test_prompt_nudge_short_prompt_with_keyword_triggers():
    out = handle_prompt_submit({"prompt": "add a new field to the Job model"})
    assert ENTRY_REMINDER in out.stdout
    assert out.stderr == ""


def test_prompt_nudge_long_prompt_without_keyword_triggers():
    long_prompt = "Please walk me through how this module decides which records to keep"
    assert len(long_prompt) > 40
    out = handle_prompt_submit({"prompt": long_prompt})
    assert ENTRY_REMINDER in out.stdout


def test_prompt_nudge_word_boundary_excludes_substrings():
    # "address" contains "add" but not as a whole word; the prompt is short
    # and has no other keyword, so it should be treated as trivial.
    out = handle_prompt_submit({"prompt": "address?"})
    assert out.stdout == ""


def test_prompt_nudge_handles_empty_prompt():
    out = handle_prompt_submit({"prompt": ""})
    assert out.stdout == ""
    out = handle_prompt_submit({})
    assert out.stdout == ""


def test_prompt_nudge_handles_non_string_prompt():
    out = handle_prompt_submit({"prompt": 42})
    assert out.stdout == ""


def test_prompt_nudge_learn_flag_appends_skip_reason():
    out = handle_prompt_submit(
        {"prompt": "implement webhook retries"},
        env={"REPOCTX_LEARN": "1"},
    )
    assert ENTRY_REMINDER in out.stdout
    assert SKIP_REASON_SUFFIX.strip() in out.stdout


def test_prompt_nudge_learn_flag_off_omits_skip_reason():
    out = handle_prompt_submit(
        {"prompt": "implement webhook retries"},
        env={"REPOCTX_LEARN": "0"},
    )
    assert SKIP_REASON_SUFFIX.strip() not in out.stdout


# -- stop-check ---------------------------------------------------------------


def _transcript(*events: dict) -> str:
    return "\n".join(json.dumps(e) for e in events) + "\n"


def _tool_use(name: str) -> dict:
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "name": name, "input": {}}],
        },
    }


def _user_message(text: str = "do the thing") -> dict:
    return {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


def test_stop_check_silent_when_no_edits(tmp_path: Path):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(_transcript(_user_message(), _tool_use("Read")))
    out = handle_stop({"transcript_path": str(transcript)})
    assert out.stdout == ""
    assert out.stderr == ""


def test_stop_check_fires_on_edits_without_validate(tmp_path: Path):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(_transcript(_user_message(), _tool_use("Edit")))
    out = handle_stop({"transcript_path": str(transcript)})
    assert EXIT_REMINDER in out.stderr
    assert out.stdout == ""


def test_stop_check_silent_when_validate_was_called(tmp_path: Path):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        _transcript(
            _user_message(),
            _tool_use("Edit"),
            _tool_use("mcp__repoctx__validate_plan"),
        )
    )
    out = handle_stop({"transcript_path": str(transcript)})
    assert out.stderr == ""


def test_stop_check_only_inspects_current_turn(tmp_path: Path):
    """A validate_plan from a prior turn does not count toward the current turn."""
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        _transcript(
            _user_message("prior turn"),
            _tool_use("Edit"),
            _tool_use("mcp__repoctx__validate_plan"),
            _user_message("new turn"),
            _tool_use("Edit"),
        )
    )
    out = handle_stop({"transcript_path": str(transcript)})
    assert EXIT_REMINDER in out.stderr


def test_stop_check_recognises_hook_style_tool_name(tmp_path: Path):
    """Older / alternate event shapes carry `tool_name` directly."""
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        _transcript(
            _user_message(),
            {"type": "tool_result", "tool_name": "Write"},
        )
    )
    out = handle_stop({"transcript_path": str(transcript)})
    assert EXIT_REMINDER in out.stderr


def test_stop_check_honors_stop_hook_active(tmp_path: Path):
    """Re-entry from inside a Stop nudge must be silent (loop guard)."""
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(_transcript(_user_message(), _tool_use("Edit")))
    out = handle_stop(
        {"transcript_path": str(transcript), "stop_hook_active": True}
    )
    assert out.stdout == ""
    assert out.stderr == ""


def test_stop_check_missing_transcript_path_is_silent():
    out = handle_stop({})
    assert out.stdout == ""
    assert out.stderr == ""


def test_stop_check_missing_transcript_file_is_silent(tmp_path: Path):
    out = handle_stop({"transcript_path": str(tmp_path / "nope.jsonl")})
    assert out.stdout == ""
    assert out.stderr == ""


def test_stop_check_skips_malformed_jsonl_lines(tmp_path: Path):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        "not json\n"
        + json.dumps(_user_message())
        + "\n"
        + "{broken\n"
        + json.dumps(_tool_use("Edit"))
        + "\n"
    )
    out = handle_stop({"transcript_path": str(transcript)})
    assert EXIT_REMINDER in out.stderr


def test_stop_check_learn_flag_appends_skip_reason(tmp_path: Path):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(_transcript(_user_message(), _tool_use("Edit")))
    out = handle_stop(
        {"transcript_path": str(transcript)}, env={"REPOCTX_LEARN": "1"}
    )
    assert EXIT_REMINDER in out.stderr
    assert SKIP_REASON_SUFFIX.strip() in out.stderr


def test_stop_check_uses_transcript_reader_override(tmp_path: Path):
    text = _transcript(_user_message(), _tool_use("Edit"))

    def reader(_path: Path) -> str:
        return text

    out = handle_stop({"transcript_path": "fake"}, transcript_reader=reader)
    assert EXIT_REMINDER in out.stderr


# -- count_turn_tool_uses primitives -----------------------------------------


def test_count_turn_falls_back_to_tail_when_no_user_message():
    # No user-role line. Should fall back to the tail-scan branch.
    text = _transcript(_tool_use("Edit"), _tool_use("Write"))
    edits, validates = count_turn_tool_uses(text)
    assert edits == 2
    assert validates == 0


@pytest.mark.parametrize(
    "tool_name,expected_edits,expected_validates",
    [
        ("Edit", 1, 0),
        ("Write", 1, 0),
        ("MultiEdit", 1, 0),
        ("Read", 0, 0),
        ("mcp__repoctx__validate_plan", 0, 1),
    ],
)
def test_count_turn_tool_use_categorisation(
    tool_name: str, expected_edits: int, expected_validates: int
):
    text = _transcript(_user_message(), _tool_use(tool_name))
    edits, validates = count_turn_tool_uses(text)
    assert edits == expected_edits
    assert validates == expected_validates


# -- CLI dispatch (end-to-end via repoctx_main.main) -------------------------


def test_cli_prompt_nudge_dispatch(monkeypatch, capsys) -> None:
    import io
    import sys as _sys

    from repoctx import main as repoctx_main

    monkeypatch.setattr(
        _sys, "argv", ["repoctx", "hook", "prompt-nudge"]
    )
    monkeypatch.setattr(
        _sys, "stdin", io.StringIO(json.dumps({"prompt": "implement webhook retries"}))
    )
    with pytest.raises(SystemExit) as exc:
        repoctx_main.main()
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert ENTRY_REMINDER in captured.out
    assert captured.err == ""


def test_cli_prompt_nudge_silent_for_trivial(monkeypatch, capsys) -> None:
    import io
    import sys as _sys

    from repoctx import main as repoctx_main

    monkeypatch.setattr(_sys, "argv", ["repoctx", "hook", "prompt-nudge"])
    monkeypatch.setattr(_sys, "stdin", io.StringIO(json.dumps({"prompt": "hi"})))
    with pytest.raises(SystemExit) as exc:
        repoctx_main.main()
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_cli_stop_check_fires_on_unverified_edits(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    import io
    import sys as _sys

    from repoctx import main as repoctx_main

    transcript = tmp_path / "session.jsonl"
    transcript.write_text(_transcript(_user_message(), _tool_use("Edit")))

    monkeypatch.setattr(_sys, "argv", ["repoctx", "hook", "stop-check"])
    monkeypatch.setattr(
        _sys,
        "stdin",
        io.StringIO(json.dumps({"transcript_path": str(transcript)})),
    )
    with pytest.raises(SystemExit) as exc:
        repoctx_main.main()
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert EXIT_REMINDER in captured.err
    assert captured.out == ""


def test_cli_stop_check_silent_with_validate(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    import io
    import sys as _sys

    from repoctx import main as repoctx_main

    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        _transcript(
            _user_message(),
            _tool_use("Edit"),
            _tool_use("mcp__repoctx__validate_plan"),
        )
    )

    monkeypatch.setattr(_sys, "argv", ["repoctx", "hook", "stop-check"])
    monkeypatch.setattr(
        _sys,
        "stdin",
        io.StringIO(json.dumps({"transcript_path": str(transcript)})),
    )
    with pytest.raises(SystemExit) as exc:
        repoctx_main.main()
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
