"""Regression tests for the B-021 fallout (B-022).

The original B-021 fix stripped NUL bytes from persisted output. That was
correct, but a provider had emitted a bare "\x00" as a *tool name*, which
serialised as the 6-character escape "\\u0000" and was therefore accepted by
providers. Once stripped it became "", and every provider rejects an empty
function name at the request level (HTTP 400), bricking the whole chat.

Note the census trap: json.dumps re-encodes NUL as the escape "\\u0000", so
scanning the DB for a raw "\x00" byte finds nothing. The NUL only becomes
real after json.loads. These tests go through the decoded objects.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cptr.utils.ai import _sanitise_tool_calls, _to_openai_messages  # noqa: E402
from cptr.utils.chat_task import (  # noqa: E402
    _drop_invalid_tool_calls,
    _output_items_to_messages,
    _strip_control_chars,
)


def _call(name, call_id="call_1", args="{}"):
    return {
        "type": "function_call",
        "id": "fc_" + call_id,
        "call_id": call_id,
        "fc_id": "",
        "name": name,
        "arguments": args,
        "status": "completed",
    }


def _out(call_id, text="ok"):
    return {
        "type": "function_call_output",
        "call_id": call_id,
        "output": text,
        "status": "completed",
    }


# ── the regression itself ────────────────────────────────────────


def test_stripping_a_nul_name_yields_empty_and_is_then_dropped():
    """A name that was ONLY a NUL becomes "" after stripping, not a valid name."""
    items = [_call("\x00", "call_bad")]

    stripped = _strip_control_chars(items)
    assert stripped[0]["name"] == ""  # the trap: empty, not "\x00"

    # ...and an empty name must never be replayed.
    kept = _drop_invalid_tool_calls(stripped)
    assert kept == []


def test_drop_invalid_tool_calls_also_drops_orphaned_output():
    items = [_call("\x00", "call_bad"), _out("call_bad", "Error: unknown tool")]
    kept = _drop_invalid_tool_calls(items)
    assert kept == [], "orphaned output for a dropped call must go too"


def test_drop_invalid_tool_calls_keeps_valid_calls_and_their_outputs():
    items = [_call("read_file", "call_ok"), _out("call_ok", "file body")]
    assert _drop_invalid_tool_calls(items) == items


def test_empty_name_is_dropped_even_with_no_control_chars():
    """Guards the case directly, not only via the strip path."""
    items = [_call("", "call_empty"), _out("call_empty")]
    assert _drop_invalid_tool_calls(items) == []


def test_nul_name_inside_a_path_survives_as_a_valid_name():
    """Stripping a NUL out of a *tool name* must not nuke a whole message."""
    items = [_call("read_file\x00", "call_a")]
    kept = _drop_invalid_tool_calls(_strip_control_chars(items))
    assert len(kept) == 1
    assert kept[0]["name"] == "read_file"


# ── history replay ──────────────────────────────────────────────


def test_history_replay_does_not_emit_empty_tool_name():
    """The exact shape from the bricked chat: name is a real NUL in memory."""
    items = [
        {"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "updating memory"}]},
        _call("\x00", "call_bad", '{"operations": []}'),
        _out("call_bad", "Error: unknown tool: \\u0000"),
        _call("list_directory", "call_ok", '{"path": "."}'),
        _out("call_ok", "README.md"),
    ]
    msgs = _output_items_to_messages(items, "mid-1")

    names = [
        tc["function"]["name"]
        for m in msgs
        for tc in (m.get("tool_calls") or [])
    ]
    assert names == ["list_directory"], f"unexpected tool_calls replayed: {names}"

    ids = [m["tool_call_id"] for m in msgs if m.get("role") == "tool"]
    assert ids == ["call_ok"], f"orphaned tool result replayed: {ids}"


# ── outbound guard (provider-facing shapes) ─────────────────────


def test_sanitise_tool_calls_drops_empty_name():
    msgs = [{"role": "assistant", "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "", "arguments": "{}"}},
    ]}]
    out = _sanitise_tool_calls(msgs)
    assert out[0]["tool_calls"] == []


def test_sanitise_tool_calls_keeps_valid_and_preserves_order():
    msgs = [{"role": "assistant", "tool_calls": [
        {"id": "call_1", "function": {"name": "read_file", "arguments": "{}"}},
        {"id": "call_2", "function": {"name": "list_directory", "arguments": "{}"}},
    ]}]
    out = _sanitise_tool_calls(msgs)
    assert [tc["function"]["name"] for tc in out[0]["tool_calls"]] == [
        "read_file", "list_directory",
    ]


def test_sanitise_tool_calls_noop_without_tool_calls():
    msgs = [{"role": "user", "content": "hi"}]
    assert _sanitise_tool_calls(msgs) == msgs


def test_to_openai_messages_never_serialises_an_empty_function_name():
    """End-to-end: whatever history we build, no empty name reaches a provider."""
    msgs = _to_openai_messages([{
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": "call_1",
            "type": "function",
            "function": {"name": "", "arguments": "{}"},
        }],
    }], "")

    for m in msgs:
        for tc in m.get("tool_calls") or []:
            assert tc["function"]["name"].strip(), "empty name would 400 the request"


def test_responses_api_input_drops_empty_named_call():
    from cptr.utils.ai import _to_responses_input

    items = _to_responses_input([{
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": "call_1",
            "function": {"name": "", "arguments": "{}"},
        }],
    }], "")

    names = [i.get("name") for i in items if i.get("type") == "function_call"]
    assert names == [], f"empty-named function_call reached Responses API: {names}"


# ── the census trap, pinned ─────────────────────────────────────


def test_raw_byte_scan_misses_an_escaped_nul():
    """Documents why a DB-wide grep for a raw NUL is not a valid check."""
    serialised = json.dumps(_call("\x00"))
    assert "\x00" not in serialised, "json.dumps escapes the NUL"
    assert "\\u0000" in serialised
    # Only decoding reveals it:
    assert json.loads(serialised)["name"] == "\x00"