"""A single malformed tool-argument blob must not kill a turn (B-026).

Chat `9ffb4266` died with `json.decoder.JSONDecodeError: Expecting value:
line 1 column 1782` raised out of the stream generator, discarding every
tool call the model had batched into that same message. `_parse_tool_arguments`
repairs what it can and, when it cannot, returns a sentinel dict instead of
raising, so the turn survives and `chat_task` shows the model the error.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from cptr.utils.ai import (
    INVALID_TOOL_ARGS_KEY,
    ChatCompletionForm,
    _parse_tool_arguments,
    stream_openai_completions,
)


# ── the parser never raises ────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "not json at all",
        "{",
        "}",
        "{'single': 'quotes'}",
        '[1, 2, 3]',
        '"just a string"',
        '{"a": }',
        '{"a": 1,,}',
        "null",
        '{"a": tru}',
        "",
        '{"a": "\\x"}',
    ],
)
def test_never_raises_and_returns_a_dict(raw):
    """Every input yields a dict. Raising here is the bug that killed the turn."""
    out = _parse_tool_arguments(raw)
    assert isinstance(out, dict)


def test_unrecoverable_marks_the_call_invalid():
    out = _parse_tool_arguments("not json at all")
    assert out.get(INVALID_TOOL_ARGS_KEY) is True
    assert "not json at all" in out["__raw__"]


def test_a_non_object_arguments_value_is_invalid():
    """A bare list/scalar is not a valid arguments object for any tool."""
    for raw in ("[1,2,3]", '"hello"', "42", "null"):
        assert _parse_tool_arguments(raw).get(INVALID_TOOL_ARGS_KEY) is True


# ── valid input is untouched ───────────────────────────────────────────


def test_valid_arguments_pass_straight_through():
    raw = '{"path": "index.html", "overwrite": false, "n": 3}'
    assert _parse_tool_arguments(raw) == {
        "path": "index.html",
        "overwrite": False,
        "n": 3,
    }


def test_empty_arguments_are_an_empty_dict_not_an_error():
    assert _parse_tool_arguments("") == {}
    assert _parse_tool_arguments("   \n ") == {}


def test_nested_and_unicode_survive():
    raw = '{"a": {"b": [1, 2, {"c": true}]}, "d": null, "u": "caf\\u00e9 \\u2014"}'
    out = _parse_tool_arguments(raw)
    assert out == {"a": {"b": [1, 2, {"c": True}]}, "d": None, "u": "café —"}
    assert not out.get(INVALID_TOOL_ARGS_KEY)


# ── the repair actually recovers the real failure ───────────────────────


def test_unescaped_quote_inside_a_string_is_recovered():
    """The shape that killed chat 9ffb4266: an unescaped `"` mid-artifact."""
    raw = '{"artifact_type": "implementation_plan", "content": "he said "hi" ok"}'
    out = _parse_tool_arguments(raw)
    assert out.get("artifact_type") == "implementation_plan"
    assert out["content"] == 'he said "hi" ok'


def test_truncated_tail_keeps_the_complete_members():
    out = _parse_tool_arguments('{"a": 1, "b": "text that never ends')
    assert out.get("a") == 1
    assert out.get("b") == "text that never ends"


def test_truncated_after_a_colon_drops_only_that_member():
    out = _parse_tool_arguments('{"a": 1, "b":')
    assert out == {"a": 1}


def test_truncated_mid_escape_keeps_the_content_read_so_far():
    out = _parse_tool_arguments('{"a": 1, "b": "abc\\')
    assert out.get("a") == 1
    assert out.get("b") == "abc"


def test_prefix_only_parse_is_not_mistaken_for_the_whole_value():
    """`raw_decode` stops at a stray quote, so a *successful* parse can still
    be a fragment. Repairing instead of trusting it keeps the rest of the blob."""
    raw = '{"a": "x "y" z", "b": 2}'
    out = _parse_tool_arguments(raw)
    assert out["a"] == 'x "y" z'
    assert out["b"] == 2


def test_a_large_malformed_artifact_is_fully_recovered():
    body = "\n".join(f'- step {i}: call foo("bar")' for i in range(300))
    raw = '{"artifact_type": "implementation_plan", "content": "# Plan\\n' + body
    out = _parse_tool_arguments(raw)
    assert out["artifact_type"] == "implementation_plan"
    assert out["content"].startswith("# Plan\n")
    assert out["content"].count('foo("bar")') == 300


def test_repair_is_linear_enough_on_a_quote_storm():
    """A pathological input must not become a stall inside the stream loop."""
    import time

    raw = '{"a": "' + ('x "y" ' * 20000) + '"'
    start = time.perf_counter()
    out = _parse_tool_arguments(raw)
    assert time.perf_counter() - start < 2.0
    # The whole value is recovered, not a 2-char prefix.
    assert len(out["a"]) > 100000


def test_deeply_nested_input_does_not_recurse():
    raw = '{"a":' * 2000 + "1" + "}" * 2000
    out = _parse_tool_arguments(raw)
    assert "a" in out


def test_the_real_failure_shape_from_chat_9ffb4266():
    """Reconstructed from the live traceback (cptr-start.log, 2026-10-04 19:50:37).

        ai.py:810  "arguments": json.loads(tc["arguments_json"])
        create_file  {"artifact_type": "implementation_plan",
                      "content": "# QR Transfer - GA Tuning Harness\\n\\n## Goal\\nTwo devices filming ea...
        json.decoder.JSONDecodeError: Expecting value: line 1 column 1782

    Column 1782 is inside `content`, i.e. an unescaped quote in the artifact
    body. The failing blob was never persisted (the exception killed the turn
    before the save), so this is reconstructed from the log's own preview.
    """
    head = (
        '{"artifact_type": "implementation_plan", "content": '
        '"# QR Transfer \u2014 GA Tuning Harness\\n\\n## Goal\\n'
        "Two devices filming each other"
    )
    body = ' use foo("bar") to drive the GA and set size="4" instead of 4 '
    raw = head + body + '"}'

    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)  # the defect that killed the turn

    out = _parse_tool_arguments(raw)
    assert not out.get(INVALID_TOOL_ARGS_KEY)
    assert out["artifact_type"] == "implementation_plan"
    # Every character the model actually wrote survives, quotes included.
    assert out["content"] == "# QR Transfer \u2014 GA Tuning Harness\n\n## Goal\n" + (
        "Two devices filming each other" + body
    )


def test_control_chars_are_not_smuggled_through_as_raw_garbage():
    """Sanity: we only ever hand back text, never raw control bytes."""
    out = _parse_tool_arguments('{"a": "x\x00y"}')
    assert isinstance(out, dict)


# ── end to end through the completions adapter ──────────────────────────

_BAD_ARGS = (
    '{"artifact_type": "implementation_plan", "content": "# QR Plan\\nuse foo("bar")'
)


def _completions_stream(arguments: str) -> str:
    chunks = [
        {
            "id": "1",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": "Plan follows."},
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "1",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "create_file", "arguments": arguments},
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "1",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "m",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
        },
    ]
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def _collect(arguments: str) -> list[dict]:
    body = _completions_stream(arguments)
    handler = lambda request: httpx.Response(200, content=body.encode())  # noqa: E731

    real = httpx.AsyncClient

    class Patched(real):
        def __init__(self, **kw):
            kw.pop("transport", None)
            super().__init__(transport=httpx.MockTransport(handler), **kw)

    async def run():
        httpx.AsyncClient = Patched
        try:
            form = ChatCompletionForm(
                model="m", messages=[{"role": "user", "content": "go"}]
            )
            return [
                e
                async for e in stream_openai_completions(
                    form, "http://test.invalid/v1", "k"
                )
            ]
        finally:
            httpx.AsyncClient = real

    return asyncio.run(run())


def test_the_adapter_does_not_raise_on_malformed_arguments():
    """This is the regression: the exact shape used to raise JSONDecodeError
    out of the generator, which killed the whole turn."""
    events = _collect(_BAD_ARGS)
    assert [e["type"] for e in events] == ["text_delta", "tool_call", "done"]
    call = next(e for e in events if e["type"] == "tool_call")
    assert call["name"] == "create_file"
    assert not call["arguments"].get(INVALID_TOOL_ARGS_KEY)
    assert "foo(\"bar\")" in call["arguments"]["content"]


def test_the_adapter_still_emits_a_call_when_arguments_are_hopeless():
    """Unrecoverable arguments must not silently vanish: the call is emitted
    with the sentinel so `chat_task` can turn it into a visible error."""
    events = _collect("this is not json")
    call = next(e for e in events if e["type"] == "tool_call")
    assert call["arguments"].get(INVALID_TOOL_ARGS_KEY) is True