"""B-023 / B-027: a truncated turn must be distinguishable from a real finish.

B-023: the provider's stop signal was read *only* to detect `tool_calls` and
then discarded, so a completion cut off at `length` was persisted byte-for-byte
like a genuine one -- a chat could "stop for no reason" and leave no trace.

B-027: auto-continue is a *consumer* of that same signal. With no reason
plumbed through, a truncated turn reached the hook looking identical to a
completion and the chain iterated on a half-answer, burning the whole run cap.

Design: ``notes/NOTES-auto-continue.md``.
"""

from __future__ import annotations

import asyncio
import ast
import pathlib
from types import SimpleNamespace

import pytest

from cptr.models import Chat, ChatMessage
from cptr.models.base import Base
from cptr.utils import ai
from cptr.utils import auto_continue as ac
from cptr.utils import db as dbmod
from cptr.utils.config import now_ms

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


def _run(coro):
    return asyncio.run(coro)


# ── The predicate ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "reason",
    ["length", "max_tokens", "max_output_tokens", "content_filter", "refusal", "incomplete"],
)
def test_truncation_reasons_are_recognised(reason):
    assert ac.is_truncated(reason) is True


@pytest.mark.parametrize("reason", ["stop", "end_turn", "tool_calls", "tool_use", ""])
def test_clean_reasons_are_not_truncation(reason):
    assert ac.is_truncated(reason) is False


def test_none_is_not_truncation():
    """Several transports report no reason at all -- the agent-target path never
    does. Treating "unknown" as truncation would silently disable auto-continue
    on every turn there, a worse bug than the one being fixed."""
    assert ac.is_truncated(None) is False


def test_predicate_is_case_and_whitespace_insensitive():
    assert ac.is_truncated("  LENGTH ") is True


# ── The transports must actually emit the reason ──────────────


def test_every_done_event_in_ai_carries_a_finish_reason():
    """Structural guard. The bug was a *missing* piece of information, so a
    behavioural test on one adapter cannot catch the other two -- in fact it
    catches none of the ones that were wrong."""
    src = pathlib.Path("cptr/utils/ai.py").read_text()
    tree = ast.parse(src)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
        if "type" not in keys:
            continue
        for key, value in zip(node.keys, node.values):
            if (
                isinstance(key, ast.Constant)
                and key.value == "type"
                and isinstance(value, ast.Constant)
                and value.value == "done"
                and "finish_reason" not in keys
            ):
                offenders.append(node.lineno)
    assert offenders == [], (
        f"done event(s) at line(s) {offenders} in cptr/utils/ai.py do not carry "
        "finish_reason; B-023 has regressed on that transport"
    )


class _FakeStreamResponse:
    """A scripted SSE body, one event per line."""

    status_code = 200

    def __init__(self, lines):
        self._lines = lines

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeClient:
    """Stands in for httpx.AsyncClient; replays `lines` for any stream()."""

    lines: list[str] = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, *a, **k):
        return _FakeStreamResponse(type(self).lines)


def _drain_completions(lines: list[str]) -> list[dict]:
    form = SimpleNamespace(
        model="m",
        messages=[{"role": "user", "content": "hi"}],
        instructions=None,
        tools=[],
    )

    async def _go():
        events = []
        async for ev in ai.stream_openai_completions(
            form_data=form,
            url="http://example.invalid/v1",
            key="k",
            request_params={},
        ):
            events.append(ev)
        return events

    original = ai.httpx.AsyncClient
    _FakeClient.lines = lines
    ai.httpx.AsyncClient = _FakeClient
    try:
        return _run(_go())
    finally:
        ai.httpx.AsyncClient = original


def test_completions_adapter_reports_a_length_stop():
    """Drive the real adapter with a stream that the provider cut short."""
    events = _drain_completions(
        [
            'data: {"choices":[{"delta":{"content":"partial ans"}}]}',
            "",
            'data: {"choices":[{"delta":{},"finish_reason":"length"}]}',
            "",
            'data: {"usage":{"prompt_tokens":5,"completion_tokens":9,"total_tokens":14}}',
            "",
            "data: [DONE]",
            "",
        ]
    )
    done = [e for e in events if e.get("type") == "done"]
    assert done, "adapter emitted no done event"
    assert done[-1].get("finish_reason") == "length", (
        "a turn truncated at `length` left the adapter looking like a genuine "
        f"finish: {done[-1]!r}"
    )


def test_completions_adapter_reports_a_normal_stop_unchanged():
    events = _drain_completions(
        [
            'data: {"choices":[{"delta":{"content":"all done"}}]}',
            "",
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
            "",
            "data: [DONE]",
            "",
        ]
    )
    done = [e for e in events if e.get("type") == "done"]
    assert done[-1].get("finish_reason") == "stop"


# ── The consumer must not iterate on a truncated turn ─────────


@pytest.fixture
def ac_db(tmp_path, monkeypatch):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'fr.db'}", poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    _run(_create())
    monkeypatch.setattr(dbmod, "_engine", engine)
    monkeypatch.setattr(dbmod, "_async_session", factory)
    monkeypatch.setattr(dbmod, "get_engine", lambda: engine)
    monkeypatch.setattr(dbmod, "get_session_factory", lambda: factory)
    yield engine


def _make_chat(meta=None) -> str:
    async def _go():
        chat = await Chat.create(
            user_id="user-1",
            title="t",
            meta=meta if meta is not None else {"params": {"auto_continue": {"enabled": True}}},
            created_at=now_ms(),
        )
        await ChatMessage.create(
            chat_id=chat.id,
            role="assistant",
            content="did some work",
            model="m1",
            done=True,
            created_at=now_ms(),
        )
        return chat.id

    return _run(_go())


def _queued(chat_id: str) -> list:
    msgs = _run(ChatMessage.get_all_by_chat(chat_id))
    return [m for m in msgs if (m.meta or {}).get("auto")]


def _queue(chat_id: str, content="done", finish_reason=None):
    return _run(
        ac.maybe_queue_auto_continue(
            chat_id=chat_id,
            assistant_message_id="assistant-1",
            model="m1",
            content=content,
            finish_reason=finish_reason,
        )
    )


class TestConsumer:
    def test_declines_a_truncated_turn(self, ac_db):
        chat_id = _make_chat()
        reason = _queue(chat_id, content="Let me track this work and ", finish_reason="length")
        assert reason is not None and "truncat" in reason.lower()
        assert _queued(chat_id) == [], (
            "a truncated turn queued a follow-up; auto-continue would iterate on "
            "a half-answer (B-027)"
        )

    def test_still_queues_a_clean_turn(self, ac_db):
        """The fix must gate the feature, not disable it."""
        chat_id = _make_chat()
        assert _queue(chat_id, finish_reason="stop") is not None
        assert len(_queued(chat_id)) == 1

    def test_transports_without_a_reason_still_queue(self, ac_db):
        """The agent-target path reports nothing; it must keep working."""
        chat_id = _make_chat()
        assert _queue(chat_id, finish_reason=None) is not None
        assert len(_queued(chat_id)) == 1

    def test_truncation_does_not_consume_the_run_budget(self, ac_db):
        """A declined turn must not advance the counter, or repeated truncations
        would silently exhaust the cap of a chat that never actually iterated."""
        chat_id = _make_chat()
        _queue(chat_id, finish_reason="length")
        chat = _run(Chat.get_by_id(chat_id))
        assert ac._state(chat)["runs"] == 0
