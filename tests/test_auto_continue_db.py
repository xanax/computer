"""DB-backed tests for the auto-continue queueing decision.

The pure helpers are covered in ``test_auto_continue.py``. What matters most is
the decision itself -- ``maybe_queue_auto_continue`` -- because it is the thing
that spends tokens unattended. These tests run it against a real (throwaway)
SQLite database so the run counter, the queued-message write and the stacking
guard are exercised end to end rather than mocked.

Design: ``notes/NOTES-auto-continue.md``.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cptr.models import Chat, ChatMessage
from cptr.models.base import Base
from cptr.utils import auto_continue as ac
from cptr.utils import db as dbmod
from cptr.utils.config import now_ms


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def ac_db(tmp_path, monkeypatch):
    """Point cptr's async session factory at a fresh SQLite file."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ac.db'}", poolclass=NullPool)
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


# ── Helpers ───────────────────────────────────────────────────


def _make_chat(meta: dict | None = None) -> str:
    """Create a chat with auto-continue on, plus one done assistant turn."""

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


def _queue(chat_id: str, content="done", output=None, model="m1"):
    return _run(
        ac.maybe_queue_auto_continue(
            chat_id=chat_id,
            assistant_message_id="assistant-1",
            model=model,
            content=content,
            output_items=output,
        )
    )


def _queued_messages(chat_id: str) -> list:
    msgs = _run(ChatMessage.get_all_by_chat(chat_id))
    return [m for m in msgs if (m.meta or {}).get("auto")]


def _runs(chat_id: str) -> int:
    chat = _run(Chat.get_by_id(chat_id))
    return ac._state(chat)["runs"]


# ── The happy path ────────────────────────────────────────────


class TestQueues:
    def test_queues_a_follow_up_when_enabled(self, ac_db):
        chat_id = _make_chat()
        assert _queue(chat_id) is not None
        queued = _queued_messages(chat_id)
        assert len(queued) == 1
        assert queued[0].role == "user"
        assert queued[0].content == ac.DEFAULT_PROMPT
        assert queued[0].meta["queued"] is True
        assert queued[0].meta["auto"] is True

    def test_uses_the_users_own_prompt(self, ac_db):
        chat_id = _make_chat({"params": {"auto_continue": {"enabled": True, "prompt": "do more"}}})
        _queue(chat_id)
        assert _queued_messages(chat_id)[0].content == "do more"

    def test_carries_the_model_so_the_next_turn_keeps_it(self, ac_db):
        chat_id = _make_chat()
        _queue(chat_id, model="special-model")
        assert _queued_messages(chat_id)[0].model == "special-model"

    def test_increments_the_run_counter(self, ac_db):
        chat_id = _make_chat()
        assert _runs(chat_id) == 0
        _queue(chat_id)
        assert _runs(chat_id) == 1

    def test_toggling_off_queues_nothing(self, ac_db):
        chat_id = _make_chat({"params": {"auto_continue": {"enabled": False}}})
        assert _queue(chat_id) is None
        assert _queued_messages(chat_id) == []

    def test_no_config_at_all_queues_nothing(self, ac_db):
        chat_id = _make_chat({"params": {}})
        assert _queue(chat_id) is None
        assert _queued_messages(chat_id) == []


# ── The safety envelope ───────────────────────────────────────


class TestStops:
    def test_run_cap_stops_the_chain(self, ac_db):
        """The cap is the backstop: it must hold even when nothing else says stop."""
        chat_id = _make_chat({"params": {"auto_continue": {"enabled": True, "max_runs": 3}}})
        for _ in range(3):
            # Clear the stacking guard so only the cap is under test.
            for m in _queued_messages(chat_id):
                _run(ChatMessage.delete(m.id))
            assert _queue(chat_id) is not None
        assert _runs(chat_id) == 3
        for m in _queued_messages(chat_id):
            _run(ChatMessage.delete(m.id))
        reason = _queue(chat_id)
        assert reason is not None and "cap" in reason
        assert _runs(chat_id) == 3  # unchanged, still capped
        assert _queued_messages(chat_id) == []

    def test_agent_stop_phrase_ends_the_chain(self, ac_db):
        chat_id = _make_chat()
        reason = _queue(chat_id, content="nothing left to iterate")
        assert reason is not None and "stop" in reason
        assert _queued_messages(chat_id) == []
        assert _runs(chat_id) == 0  # not even counted

    def test_stop_phrase_in_output_items_ends_the_chain(self, ac_db):
        chat_id = _make_chat()
        output = [
            {"type": "message", "content": [{"type": "output_text", "text": "nothing left to iterate"}]}
        ]
        assert "stop" in (_queue(chat_id, content="", output=output) or "")
        assert _queued_messages(chat_id) == []

    def test_does_not_stack_a_second_follow_up(self, ac_db):
        chat_id = _make_chat()
        assert _queue(chat_id) is not None
        reason = _queue(chat_id)
        assert reason is not None and "already queued" in reason
        assert len(_queued_messages(chat_id)) == 1
        assert _runs(chat_id) == 1  # the declined attempt did not count

    def test_a_deleted_queued_prompt_re_arms_the_next_one(self, ac_db):
        chat_id = _make_chat()
        _queue(chat_id)
        for m in _queued_messages(chat_id):
            _run(ChatMessage.delete(m.id))
        assert _queue(chat_id) is not None
        assert len(_queued_messages(chat_id)) == 1

    @pytest.mark.parametrize("marker", [{"internal": True}, {"subagent": True}])
    def test_internal_chats_never_auto_continue(self, ac_db, marker):
        """A subagent that re-prompts itself would fork agents forever."""
        chat_id = _make_chat({"internal": True, "params": {"auto_continue": {"enabled": True}}})
        if marker == {"subagent": True}:
            chat_id = _make_chat(
                {"subagent": True, "params": {"auto_continue": {"enabled": True}}}
            )
        assert _queue(chat_id) is None
        assert _queued_messages(chat_id) == []


# ── Re-arming ─────────────────────────────────────────────────


class TestResetRuns:
    def test_reset_clears_the_counter(self, ac_db):
        chat_id = _make_chat({"params": {"auto_continue": {"enabled": True, "max_runs": 1}}})
        _queue(chat_id)
        for m in _queued_messages(chat_id):
            _run(ChatMessage.delete(m.id))
        _queue(chat_id)
        assert _runs(chat_id) == 1

        _run(ac.reset_runs(chat_id))
        assert _runs(chat_id) == 0
        for m in _queued_messages(chat_id):
            _run(ChatMessage.delete(m.id))
        assert _queue(chat_id) is not None

    def test_reset_is_a_no_op_when_disabled(self, ac_db):
        chat_id = _make_chat({"params": {"auto_continue": {"enabled": False}}})
        _run(ac.reset_runs(chat_id))
        assert _runs(chat_id) == 0

    def test_reset_is_a_no_op_when_the_config_is_gone(self, ac_db):
        chat_id = _make_chat({"params": {}})
        _run(ac.reset_runs(chat_id))
        assert _runs(chat_id) == 0

    def test_reset_preserves_other_meta_keys(self, ac_db):
        """The counter must merge, not overwrite: a save of state is not a
        reason to drop last_model, params or anything else on the chat."""
        chat_id = _make_chat(
            {"last_model": "m1", "pinned": True, "params": {"auto_continue": {"enabled": True}}}
        )
        _queue(chat_id)
        _run(ac.reset_runs(chat_id))
        chat = _run(Chat.get_by_id(chat_id))
        assert chat.meta["last_model"] == "m1"
        assert chat.meta["pinned"] is True
        assert chat.meta["params"]["auto_continue"]["enabled"] is True

    def test_reset_does_not_zero_updated_at(self, ac_db):
        """`update_meta`'s default `updated_at=0` zeroes the column rather than
        leaving it, which would make the chat look freshly touched on every
        queued prompt. Guarded here so the call site keeps passing now_ms()."""
        chat_id = _make_chat()
        before = _run(Chat.get_by_id(chat_id)).updated_at
        _queue(chat_id)
        assert _run(Chat.get_by_id(chat_id)).updated_at > before


# ── Resilience ────────────────────────────────────────────────


class TestNeverRaises:
    def test_missing_chat_is_harmless(self, ac_db):
        assert _run(
            ac.maybe_queue_auto_continue(
                chat_id="no-such-chat",
                assistant_message_id="x",
                model="m1",
                content="hi",
            )
        ) is None

    def test_a_db_failure_does_not_propagate(self, ac_db, monkeypatch):
        """A broken follow-up must never disturb a turn that already succeeded."""
        chat_id = _make_chat()

        async def _boom(_chat_id, _meta, _updated_at=0):
            raise RuntimeError("db down")

        monkeypatch.setattr(Chat, "update_meta", staticmethod(_boom))
        assert _queue(chat_id) is None  # swallowed, returns None
