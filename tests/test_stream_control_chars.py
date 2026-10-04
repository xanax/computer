"""Control-character guard on streamed model output.

Design: the corrupted transcript in chat
``0717880f-8203-4cca-93c0-c13b6a0d0214`` (see ``BUGS.md``).

A provider route (MiniMax behind OpenRouter, reached via
``openrouter/stealth/space-bunny-alpha``) emitted NUL bytes at token
boundaries inside tool-call arguments and assistant text::

    "cd /home/br\\x00endan/computer && sqlite3 ..."
    "cptr/\\x00frontend/src/lib/stores"

They survive JSON encoding, so cptr persisted them and handed them straight
to tools, where the only visible symptom was ``Error: embedded null byte`` /
``Error: lstat: embedded null character in path``. The agent then retried the
same broken call, which read to the user as "it keeps crashing".

These tests pin the two guards: the live stream and the persisted-``output``
replay (which re-enters the prompt context on every later turn).
"""

from __future__ import annotations

from cptr.utils.chat_task import (
    _had_control_chars,
    _output_items_to_messages,
    _strip_control_chars,
)


def test_strips_nul_from_tool_arguments():
    # The real failing call: NUL inside a path.
    assert _strip_control_chars(
        {"command": "cd /home/br\x00endan/computer && ls"}
    ) == {"command": "cd /home/brendan/computer && ls"}


def test_strips_nul_mid_token_in_output_text():
    assert _strip_control_chars("Not done yet.]<][0Let me check") == "Not done yet.]<][0Let me check"


def test_strips_all_c0_controls_except_newline_and_tab():
    # \n and \t are deliberately kept: tool output is full of both, and
    # blanking them would mangle legitimate file listings.
    raw = "a\x00b\x07c\x1bd\ne\tf\x7fg"
    assert _strip_control_chars(raw) == "abcd\ne\tfg"


def test_leaves_clean_values_untouched():
    clean = {"command": "ls -la", "path": "cptr/utils", "note": "keep ✅ unicode"}
    assert _strip_control_chars(clean) == clean


def test_recurses_into_lists_and_dicts():
    event = {
        "type": "tool_call",
        "arguments": {"files": ["a\x00b", {"nested": "c\x00d"}]},
    }
    assert _strip_control_chars(event) == {
        "type": "tool_call",
        "arguments": {"files": ["ab", {"nested": "cd"}]},
    }


def test_had_control_chars_detects_only_dirty_values():
    assert _had_control_chars({"a": "x\x00y"}) is True
    assert _had_control_chars({"a": "clean", "b": ["ok", {"c": "fine"}]}) is False
    # Non-string leaves pass through untouched rather than being rebuilt.
    assert _strip_control_chars({"n": 5, "b": True, "z": None}) == {"n": 5, "b": True, "z": None}


def test_persisted_output_replay_is_cleaned():
    # `m.output` is the source of truth for prompt reconstruction, so a
    # pre-existing bad row would re-inject the NUL on every future turn.
    items = [
        {
            "type": "function_call",
            "status": "completed",
            "call_id": "call_1",
            "name": "run_command",
            "arguments": {"command": "cd /home/br\x00endan/computer"},
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "listing"},
    ]
    messages = _output_items_to_messages(items)
    replayed = repr(messages)
    assert "\x00" not in replayed
    assert "brendan" in replayed
