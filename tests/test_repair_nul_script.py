"""Tests for scripts/repair-nul-control-chars.py.

Two hard-won rules this pins:

1. A NUL in a persisted column is stored as the ESCAPE "\\u0000", not a raw
   byte, so a column-level byte scan reports "clean" while the value is
   corrupt. The repair must decode before judging.
2. Control characters in a *tool output body* are usually real data (ANSI
   colour escapes, a binary blob fetched by read_url). Rewriting those
   destroys content, so the repair must leave them alone.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "repair_nul", ROOT / "scripts" / "repair-nul-control-chars.py"
)
repair_nul = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(repair_nul)


def _call(name, call_id="call_1", arguments=None):
    return {
        "type": "function_call",
        "id": "fc_" + call_id,
        "call_id": call_id,
        "fc_id": "",
        "name": name,
        "arguments": arguments if arguments is not None else {},
        "status": "completed",
    }


def _msg(text):
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    }


def _out(body, call_id="call_1"):
    return {"type": "function_call_output", "call_id": call_id, "output": body}


# ── rule 1: decode, don't scan raw bytes ────────────────────────


def test_escaped_nul_in_assistant_text_is_found():
    stored = json.dumps([_msg("the chat API to\x00 and the backend")])
    assert "\x00" not in stored, "the column holds the escape, not a byte"
    new, hits, dropped, _ = repair_nul.repair_output(stored)
    assert hits == 1
    assert "\x00" not in json.loads(new)[0]["content"][0]["text"]


def test_nul_in_tool_call_arguments_is_found():
    """Arguments are a dict, not a string — a string-only check misses them."""
    stored = json.dumps([
        _call("run_command", arguments={"command": "cd /home/br\x00endan/computer"})
    ])
    new, hits, dropped, _ = repair_nul.repair_output(stored)
    assert hits == 1
    assert json.loads(new)[0]["arguments"] == {"command": "cd /home/brendan/computer"}


def test_nul_in_reasoning_and_reasoning_details_is_found():
    stored = json.dumps([{
        "type": "reasoning",
        "content": [{"type": "output_text", "text": "checking the \x00 endpoint"}],
        "reasoning_details": [{"type": "reasoning.text", "text": "see \x00"}],
    }])
    new, hits, _, _ = repair_nul.repair_output(stored)
    assert hits == 2
    item = json.loads(new)[0]
    assert "\x00" not in json.dumps(item)


# ── rule 2: leave real tool output alone ────────────────────────


def test_ansi_colour_codes_in_tool_output_are_preserved():
    body = "\x1b[1;31mError: \x1b[0mpermission denied"
    stored = json.dumps([_call("run_command"), _out(body)])
    new, hits, dropped, skipped = repair_nul.repair_output(stored)
    assert skipped is True
    assert hits == 0 and dropped == 0
    assert new == stored, "ANSI colour is real data and must not be rewritten"


def test_binary_payload_in_tool_output_is_preserved():
    blob = "".join(chr(i % 256) for i in range(2000))
    stored = json.dumps([_call("read_url"), _out(blob)])
    new, hits, dropped, skipped = repair_nul.repair_output(stored)
    assert hits == 0 and skipped is True
    assert json.loads(new)[1]["output"] == blob


def test_clean_row_is_returned_unchanged():
    stored = json.dumps([_msg("all good"), _call("read_file"), _out("body")])
    new, hits, dropped, _ = repair_nul.repair_output(stored)
    assert (hits, dropped) == (0, 0)
    assert new == stored


# ── invalid tool calls ──────────────────────────────────────────


def test_call_whose_name_is_only_a_nul_is_dropped_with_its_output():
    stored = json.dumps([_call("\x00", "call_bad"), _out("Error: unknown tool", "call_bad")])
    new, hits, dropped, _ = repair_nul.repair_output(stored)
    assert dropped == 1
    assert json.loads(new) == [], "call and its orphaned output both go"


def test_valid_call_survives_alongside_its_output():
    stored = json.dumps([_call("read_file", "call_ok"), _out("body", "call_ok")])
    new, hits, dropped, _ = repair_nul.repair_output(stored)
    assert (hits, dropped) == (0, 0)
    assert json.loads(new)[0]["name"] == "read_file"


def test_name_with_embedded_nul_is_repaired_not_dropped():
    stored = json.dumps([_call("read_file\x00", "call_a")])
    new, hits, dropped, _ = repair_nul.repair_output(stored)
    assert dropped == 0
    assert json.loads(new)[0]["name"] == "read_file"


# ── robustness ──────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["", "null", "not json [", '{"not":"a list"}'])
def test_unparseable_or_non_list_output_is_left_alone(bad):
    new, hits, dropped, _ = repair_nul.repair_output(bad)
    assert new == bad
    assert (hits, dropped) == (0, 0)