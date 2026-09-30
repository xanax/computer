"""Plan-mode ``create_artifact`` calls must pass ``__context__``, not ``workspace``.

Ledger: B-016.

``create_artifact`` is registered inline for plan mode only, so it is not in
``ALL_TOOLS`` and cannot go through ``execute_tool()`` — its two call sites in
``run_chat_task`` invoke it directly. When its signature moved from
``workspace: str`` to ``__context__: dict`` those two call sites were missed, so
every plan-mode artifact raised ``TypeError: create_artifact() got an unexpected
keyword argument 'workspace'``. The raise happened *outside* ``execute_tool``'s
try/except, so it escaped the tool call and aborted the whole turn — the user saw
a finished reply with ``> **Error:**`` appended and no artifact.

These tests pin the call shape (so the next signature change cannot silently
re-break it) and pin the guard (a tool error must not kill the turn).
"""

from __future__ import annotations

import asyncio
import inspect

from cptr.utils import chat_task
from cptr.utils.chat_task import _execute_create_artifact
from cptr.utils.tools import create_artifact

CONTEXT = {"workspace": "/tmp/cptr-test-workspace", "request": None, "chat_id": "c1"}


def test_the_direct_call_matches_the_real_tool_signature():
    """What the helper builds must bind against create_artifact as it exists now.

    This is the test that fails the moment ``create_artifact`` changes shape
    again — ``bind`` raises TypeError for an unknown keyword, exactly as the
    live call did.
    """
    args = {"content": "# plan", "artifact_type": "implementation_plan", "title": "T"}
    inspect.signature(create_artifact).bind(**args, __context__=CONTEXT)


def test_no_call_site_in_chat_task_passes_workspace_to_create_artifact():
    """The bug itself: this fails against the pre-fix source.

    A signature check on the helper alone cannot see the call sites, and the
    call sites are where the wrong keyword lived — twice (the queued/resume path
    and the auto-approved path). Parsed rather than grepped, so a mention in
    prose or a comment cannot pass for a call.
    """
    import ast
    from pathlib import Path

    source = Path(chat_task.__file__).read_text(encoding="utf-8")
    offenders = []

    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "create_artifact":
            continue
        offenders += [
            (node.lineno, kw.arg) for kw in node.keywords if kw.arg == "workspace"
        ]

    assert offenders == [], f"create_artifact called with workspace= at {offenders}"


def test_helper_forwards_context_and_drops_a_model_supplied_workspace(monkeypatch):
    """The model may invent a ``workspace`` argument; it must not reach the tool."""
    seen: dict = {}

    async def fake_create_artifact(**kwargs):
        seen.update(kwargs)
        return "{}"

    monkeypatch.setattr(chat_task, "create_artifact", fake_create_artifact)
    out = asyncio.run(
        _execute_create_artifact(
            {"content": "# plan", "artifact_type": "implementation_plan", "workspace": "/wrong"},
            CONTEXT,
        )
    )

    assert out == "{}"
    assert seen["__context__"] is CONTEXT
    assert "workspace" not in seen
    assert seen["content"] == "# plan"


def test_a_failing_artifact_call_reports_instead_of_raising(monkeypatch):
    """A tool error is a tool result, not a dead turn (the old failure mode)."""

    async def boom(**kwargs):
        raise TypeError("create_artifact() got an unexpected keyword argument 'workspace'")

    monkeypatch.setattr(chat_task, "create_artifact", boom)
    out = asyncio.run(_execute_create_artifact({"content": "x"}, CONTEXT))

    assert out.startswith("Error executing create_artifact: ")
    assert "unexpected keyword argument 'workspace'" in out


def test_missing_request_context_is_an_error_string_not_a_raise():
    """End to end against the real tool, with no live request to write through."""
    out = asyncio.run(_execute_create_artifact({"content": "# plan"}, CONTEXT))

    assert out == "Error: request context unavailable"
