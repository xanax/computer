"""Timers: `at` parsing, and the fold for timers that predate the job queue.

A timer used to be a *dormant internal child chat*: a `Chat` row carrying
`meta.type='timer'`, woken by its own worker loop (`timer_worker_loop`), which
polled every second and wrote a user message plus an assistant placeholder into
the parent chat. As of the unified job queue, `timer()` writes a `trigger='at'`
job instead (`cptr/utils/tools.py`), so there is nothing left to poll and that
loop is gone.

What remains here:

- `parse_timer_at` — the `at` grammar (`10s`, `in 5 minutes`, RFC 3339), shared
  by the `timer` tool and `defer_workspace_todo`;
- `fold_legacy_timers` — a boot sweep that moves any child chat that was already
  waiting into `jobs`, so a pre-0010 timer still fires.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime

logger = logging.getLogger(__name__)

_RELATIVE_TIME = re.compile(
    r"^(?:\+|in\s+)?(\d+)\s*(s|sec(?:onds?)?|m|min(?:utes?)?|h|hours?|d|days?)$"
)
_RFC3339_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")
_TIME_UNITS_NS = {
    "s": 1_000_000_000,
    "m": 60 * 1_000_000_000,
    "h": 60 * 60 * 1_000_000_000,
    "d": 24 * 60 * 60 * 1_000_000_000,
}


def parse_timer_at(value: str) -> int:
    """Normalize a relative offset or timezone-aware RFC 3339 timestamp."""
    raw = value.strip()
    now = time.time_ns()
    relative = _RELATIVE_TIME.fullmatch(raw.lower())
    if relative:
        count = int(relative.group(1))
        if count <= 0:
            raise ValueError("at must be in the future.")
        return now + count * _TIME_UNITS_NS[relative.group(2)[0]]

    if not _RFC3339_TIME.fullmatch(raw):
        raise ValueError(
            "at must be a relative time such as 10s or in 10 seconds, "
            "or an RFC 3339 timestamp with a timezone."
        )
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            "at must be a relative time such as 10s or in 10 seconds, "
            "or an RFC 3339 timestamp with a timezone."
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("absolute at values must include an explicit timezone.")

    due_at = int(parsed.timestamp() * 1_000_000_000)
    if due_at <= now:
        raise ValueError("at must be in the future.")
    return due_at


async def _set_timer_status(chat_id: str, status: str, **fields) -> None:
    from cptr.models import Chat
    from cptr.utils.config import now_ms

    chat = await Chat.get_by_id(chat_id)
    if not chat:
        return
    meta = dict(chat.meta or {})
    meta["status"] = status
    meta.update(fields)
    await Chat.update_meta(chat_id, meta, now_ms())


async def fold_legacy_timers() -> int:
    """Move pre-0010 timers that are still waiting into the job queue.

    Old timers survive as dormant child chats, but nothing creates or polls
    those any more, so one left mid-wait would never wake. Each pending row
    becomes the job it would have become anyway: same instant, same model, same
    destination chat, same `cancel_on` semantics (`Job.cancel_cancellable` now
    enforces those on the job row). The chat is then marked `folded` so a second
    boot does not duplicate the work, and rows caught mid-launch by a restart
    are settled as errors, as `recover_timers` used to do.

    Returns how many timers were folded, for the boot log.
    """
    from cptr.models import Chat, ChatMessage
    from cptr.models.jobs import KIND_TASK, STATUS_OPEN, TRIGGER_AT, Job

    from cptr.utils.config import now_ms

    folded = 0
    for timer in await Chat.get_timers("pending"):
        meta = dict(timer.meta or {})
        due_at = int(meta.get("timer_at") or 0)
        parent_chat = meta.get("parent_chat_id") or ""
        message = await ChatMessage.get_by_id(timer.current_message_id or "")
        if message is not None and message.role != "user":
            # A launch interrupted by a restart left the child chat pointing at
            # an empty assistant placeholder; the prompt is its parent, and the
            # placeholder is dropped so the chat does not look mid-thought.
            prompt_message = await ChatMessage.get_by_id(message.parent_id or "")
            if not message.done:
                await ChatMessage.delete(message.id)
                await Chat.update_current_message(
                    timer.id, message.parent_id or "", now_ms()
                )
            message = prompt_message
        content = (message.content or "") if message else ""
        if not due_at or not parent_chat or not content.strip():
            await _set_timer_status(
                timer.id,
                "error",
                timer_error="folded at boot: timer is missing its time, chat or prompt",
            )
            continue

        job = await Job.create(
            user_id=timer.user_id,
            workspace=meta.get("workspace") or "",
            title=content.strip()[:120],
            kind=KIND_TASK,
            executor=meta.get("timer_model_id") or "human",
            trigger=TRIGGER_AT,
            trigger_at=due_at,
            status=STATUS_OPEN,
            payload=content,
            parent_chat=parent_chat,
            source="chat",
            origin_chat=parent_chat,
            meta={
                # Same markers the `timer` tool writes, so this behaves as a
                # late message to the parent chat rather than a fresh run.
                "timer": True,
                "cancel_on": list(meta.get("cancel_on") or []),
                "origin_message_id": meta.get("timer_parent_message_id"),
                "folded_from_timer_chat": timer.id,
            },
        )
        await _set_timer_status(
            timer.id,
            "folded",
            folded_into_job=job.id,
            timer_folded_at=time.time_ns(),
        )
        folded += 1

    for timer in await Chat.get_timers("running"):
        await _set_timer_status(
            timer.id,
            "error",
            timer_completed_at=time.time_ns(),
            timer_error="interrupted by restart",
        )

    if folded:
        logger.info("Folded %d legacy timer(s) into the job queue", folded)
    return folded
