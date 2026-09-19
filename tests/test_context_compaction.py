"""Tests for chat-history assembly across compaction checkpoints.

A compaction checkpoint is a ``chat_summary`` stamped on one message of a
branch; it summarises everything *before* that message. Loading history for a
branch therefore has to answer one question: when a branch carries several
checkpoints, which one bounds the replay? These tests pin that down without a
database by patching ``ChatMessage.get_all_by_chat``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from cptr.models.chats import ChatMessage
from cptr.utils import chat_task


def _run(coro):
    return asyncio.run(coro)


def _msg(mid: str, parent_id: str | None, role: str, summary: str | None = None):
    return SimpleNamespace(
        id=mid,
        parent_id=parent_id,
        role=role,
        content=f"{role}:{mid}",
        chat_summary=summary,
        done=True,
        output=None,
        meta=None,
        usage=None,
        model=None,
        created_at=0,
    )


@pytest.fixture
def history(monkeypatch):
    """Install a fake message table and return a loader for it."""

    def install(messages):
        async def fake_get_all_by_chat(chat_id):
            return messages

        monkeypatch.setattr(ChatMessage, "get_all_by_chat", fake_get_all_by_chat)
        return messages

    return install


def test_without_checkpoint_the_whole_branch_is_replayed(history):
    history(
        [
            _msg("u1", None, "user"),
            _msg("a1", "u1", "assistant"),
            _msg("u2", "a1", "user"),
        ]
    )
    messages, summary = _run(chat_task._load_message_history("c", "u2"))

    assert [m["id"] for m in messages] == ["u1", "a1", "u2"]
    assert summary is None


def test_newest_checkpoint_wins(history):
    """Each summary absorbs the previous one, so the latest bounds the replay."""
    history(
        [
            _msg("u1", None, "user"),
            _msg("a1", "u1", "assistant"),
            _msg("u2", "a1", "user", summary="summary of u1..a1"),
            _msg("a2", "u2", "assistant"),
            _msg("u3", "a2", "user", summary="summary of u1..a2"),
            _msg("a3", "u3", "assistant"),
        ]
    )
    messages, summary = _run(chat_task._load_message_history("c", "a3"))

    assert [m["id"] for m in messages] == ["u3", "a3"]
    assert summary == "summary of u1..a2"


def test_checkpoint_message_itself_is_replayed(history):
    """The checkpoint bounds the replay but is not swallowed by it."""
    history(
        [
            _msg("u1", None, "user"),
            _msg("u2", "u1", "user", summary="the only summary"),
        ]
    )
    messages, summary = _run(chat_task._load_message_history("c", "u2"))

    assert [m["id"] for m in messages] == ["u2"]
    assert summary == "the only summary"


def test_checkpoint_on_a_sibling_branch_is_ignored(history):
    """Regenerating from before a checkpoint must not resurrect its summary."""
    history(
        [
            _msg("u1", None, "user"),
            _msg("a1", "u1", "assistant", summary="summarises u1"),
            # u2/a2 live on a sibling branch of u2b: only 'a1' is an ancestor.
            _msg("u2", "a1", "user"),
            _msg("a2", "u2", "assistant", summary="sibling-only summary"),
            _msg("u2b", "a1", "user"),
            _msg("a2b", "u2b", "assistant"),
        ]
    )
    messages, summary = _run(chat_task._load_message_history("c", "a2b"))

    assert [m["id"] for m in messages] == ["a1", "u2b", "a2b"]
    assert summary == "summarises u1"


def test_message_payload_marks_a_checkpoint_without_sending_the_summary():
    """The transcript draws its divider from `summary_chars`, not from the text.

    The summary itself is ~3 KB and is replayed through the system prompt, so it
    must stay out of the per-message payload that loadChat returns.
    """
    from cptr.routers.chat import _message_dict

    checkpoint = _msg("u6", "a5", "user", summary="x" * 3000)
    plain = _msg("a6", "u6", "assistant")

    marked = _message_dict(checkpoint)
    assert marked["summary_chars"] == 3000
    assert "chat_summary" not in marked

    assert _message_dict(plain)["summary_chars"] == 0


def test_checkpoint_id_is_the_user_turn_that_starts_the_keep_zone():
    """The reload point and the checkpoint must agree, or replay loses turns."""
    keep_zone = [
        {"id": "a5", "role": "assistant", "content": ""},
        {"id": "u6", "role": "user", "content": ""},
        {"id": "a6", "role": "assistant", "content": ""},
    ]
    assert chat_task._summary_checkpoint_message_id(keep_zone, "leaf") == "u6"
    # No user turn at all: fall back to the first message with an id.
    assert chat_task._summary_checkpoint_message_id(keep_zone[:1], "leaf") == "a5"
    assert chat_task._summary_checkpoint_message_id([], "leaf") == "leaf"
