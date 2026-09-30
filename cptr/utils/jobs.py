"""Job scheduler: fire deferred work and report how it landed.

Phase 2 of ``notes/NOTES-myriad-job-queue.md``. The clock is the only trigger
implemented here — ``trigger='at'``, one shot, which is what "defer this todo to
a model at 03:00" needs. ``rrule``/``window`` and ``resource`` lanes (Phases 3-4)
are deliberately absent; the scheduler is written so they slot in beside the
claim, not through it.

Two destinations, one shape:

- ``parent_chat`` set  → wake *that* conversation (timer semantics);
- ``parent_chat`` null → start a fresh chat titled after the job (automation
  semantics, and the default for a deferred todo).

The invariant that makes unattended runs safe (design note §7): **agents never
land writes, they land proposed changes.** A finished run therefore ends
``needs_review``, never ``done`` — ``done`` is reserved for a human. The agent
inside the run proposes closing the todo, and that proposal goes through
``todo_requests`` for approval like any other chat-proposed change.

Environment:
    JOB_POLL_INTERVAL         – seconds between polls (default: 2)
    JOB_TOOL_APPROVAL_MODE    – approval mode for a job's fresh chat (default: full)
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from cptr.env import JOB_POLL_INTERVAL, JOB_TOOL_APPROVAL_MODE
from cptr.models.jobs import (
    EXECUTOR_HUMAN,
    KIND_NOTE,
    KIND_RUN,
    STATUS_FAILED,
    STATUS_NEEDS_REVIEW,
    STATUS_OPEN,
    STATUS_QUEUED,
    STATUS_RUNNING,
    Job,
)

logger = logging.getLogger(__name__)

#: A run that outlives this is settled as failed; nothing can outlive a restart.
_RUN_TIMEOUT_S = 6 * 60 * 60
_WATCH_INTERVAL_S = 5.0
#: Consecutive failed reads of a run's row before the watcher gives up on it.
_MAX_READ_FAILURES = 20
#: Sentinel error for "the chat this job wants is mid-turn"; the job retries.
_BUSY = "parent chat is busy"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _iso(ns: int | None) -> str:
    if not ns:
        return "now"
    stamp = datetime.fromtimestamp(ns / 1_000_000_000, timezone.utc)
    return stamp.isoformat().replace("+00:00", "Z")


def build_prompt(job: Job) -> str:
    """The user message a scheduled job hands to the agent.

    Three kinds of row reach here and they want different framing: a deferred
    todo (this is a promise I made to myself), a firing of a recurring task
    (this happens every day whether or not anyone is looking), and a webhook
    call (something outside asked for this just now).
    """
    if (job.meta or {}).get("timer"):
        # A timer is a message the human wrote *to their own chat* and asked to
        # have delivered later. It should arrive as what they wrote, not with a
        # robot preamble explaining that it is now later.
        return (job.payload or job.title).strip()

    is_run = job.kind == KIND_RUN
    header = (
        "[Scheduled run - a recurring task just fired]"
        if is_run
        else "[Scheduled job - a deferred workspace todo just fired]"
    )
    when = job.trigger_at or (job.meta or {}).get("occurrence_scheduled_for")
    lines = [
        header,
        "",
        f"Task: {(job.payload or job.title).strip()}",
        f"Workspace: {job.workspace}",
        f"Scheduled for: {_iso(when)}",
    ]
    if job.origin_chat:
        lines.append(f"Proposed in chat: {job.origin_chat}")

    payload = (job.meta or {}).get("webhook_payload")
    if payload:
        lines += [
            "",
            "The caller sent this payload with the request:",
            "```json",
            str(payload),
            "```",
        ]

    lines += [
        "",
        "Nobody is watching this chat: no human will answer a question, and no one",
        "will approve a tool call, so finish what you can and leave the rest plainly",
        "unfinished rather than guessing.",
        "",
        "Your work lands in a review queue, not a done pile - do not treat the task",
        "as verified. If you changed files, say plainly which ones and what the change",
        "does.",
    ]
    if is_run:
        # Closing the *template* would cancel the schedule, so a run must not
        # offer that as the way to say "this one went fine".
        lines.append(
            "This is one firing of a recurring task; the schedule itself is not"
            " yours to change. Report the outcome in your final message - do not"
            " try to close the recurring task."
        )
    else:
        lines.append(
            "If it really is complete, propose closing it with"
            f' complete_workspace_todo(todo_id="{job.id}"), which the human approves'
            " in the workspace dashboard."
        )
    return "\n".join(lines)


async def _new_chat_for_job(request, job: Job):
    """Create the chat a job with no parent conversation runs inside."""
    from cptr.models import Chat
    from cptr.utils.chat_export import export_chat_to_file
    from cptr.utils.runtime import Runtime

    chat = await Chat.create(
        user_id=job.user_id,
        title=job.title[:120],
        meta={
            "workspace": job.workspace,
            "job_id": job.id,
            "params": {"tool_approval_mode": JOB_TOOL_APPROVAL_MODE},
        },
        created_at=_now_ms(),
    )

    # A workspace-scoped chat needs its on-disk marker for the sidebar and
    # export paths to find it (automations do the same).
    marker = Path(job.workspace) / ".cptr" / "chats" / f"{chat.id}.json"
    try:
        await Runtime.write_file(request, str(marker), "{}")
    except Exception:
        logger.debug("Job %s: could not write chat marker", job.id[:8], exc_info=True)
    await export_chat_to_file(request, chat.id)
    return chat


async def _wake_parent_chat(job: Job):
    """Add the job's turn to its parent conversation. Returns (msg, error)."""
    from cptr.models import Chat, ChatMessage
    from cptr.utils.chat_task import get_pending_input_lock

    parent = await Chat.get_by_id(job.parent_chat or "")
    if not parent:
        return None, "parent chat no longer exists"

    async with get_pending_input_lock(parent.id):
        messages = await ChatMessage.get_all_by_chat(parent.id)
        if any(m.role == "assistant" and not m.done for m in messages):
            # Someone is mid-turn in that chat; do not interleave a job into it.
            return None, _BUSY

        done = [m for m in messages if m.role == "assistant" and m.done]
        prompt_msg = await ChatMessage.create(
            chat_id=parent.id,
            role="user",
            content=build_prompt(job),
            parent_id=done[-1].id if done else None,
            model=job.executor,
            meta={"internal": True, "type": "job"},
            created_at=_now_ms(),
        )
        assistant_msg = await ChatMessage.create(
            chat_id=parent.id,
            role="assistant",
            content="",
            parent_id=prompt_msg.id,
            model=job.executor,
            done=False,
            created_at=_now_ms(),
        )
        await Chat.update_current_message(parent.id, assistant_msg.id, _now_ms())
    return assistant_msg, ""


async def _prepare_run(app, job: Job):
    """Get the run ready. Returns ``(chat, assistant_msg, target, error)``."""
    from cptr.models import Chat, ChatMessage
    from cptr.utils.identity import internal_request_for_user
    from cptr.utils.model_targets import resolve_model_target

    try:
        target = await resolve_model_target(job.executor, app.state)
    except Exception as exc:  # a model can be removed while its job waits
        return None, None, None, f"model unavailable: {exc}"

    request = await internal_request_for_user(app, job.user_id)

    if job.parent_chat:
        assistant_msg, error = await _wake_parent_chat(job)
        if not assistant_msg:
            return None, None, None, error
        chat = await Chat.get_by_id(job.parent_chat)
        return chat, assistant_msg, target, ""

    chat = await _new_chat_for_job(request, job)
    prompt_msg = await ChatMessage.create(
        chat_id=chat.id,
        role="user",
        content=build_prompt(job),
        model=target.full_model_id,
        created_at=_now_ms(),
    )
    assistant_msg = await ChatMessage.create(
        chat_id=chat.id,
        role="assistant",
        content="",
        parent_id=prompt_msg.id,
        model=target.full_model_id,
        done=False,
        created_at=_now_ms(),
    )
    await Chat.update_current_message(chat.id, assistant_msg.id, _now_ms())
    return chat, assistant_msg, target, ""


async def _tell_ui(job_id: str) -> None:
    """Best-effort `jobs_changed`, so an open dashboard moves without a reload.

    A settled run is a state change a human waits on; losing the event costs a
    refresh, so it never raises into the watcher.
    """
    from cptr.socket.main import emit_jobs_changed

    try:
        job = await Job.get_by_id(job_id)
        if job:
            await emit_jobs_changed(job.user_id, job.workspace)
    except Exception:
        logger.debug("Job %s: could not emit jobs_changed", job_id[:8], exc_info=True)


async def _after_settle(job_id: str) -> None:
    """Everything that must happen once a run reaches a terminal status.

    The legacy mirror is kept in step here rather than at each call site, so the
    two tables cannot drift while both exist (migration 0010).
    """
    from cptr.utils.task_scheduler import sync_legacy_run_status

    await sync_legacy_run_status(job_id)
    await _tell_ui(job_id)


async def _watch_run(job_id: str, message_id: str) -> None:
    """Settle a job once its run finishes: `needs_review`, or `failed`.

    ``run_chat_task`` swallows its own exceptions and records failure on the
    assistant message, so the outcome is read from the message, not from a
    raised error.
    """
    from cptr.models import ChatMessage

    waited = 0.0
    read_failures = 0
    while waited < _RUN_TIMEOUT_S:
        await asyncio.sleep(_WATCH_INTERVAL_S)
        waited += _WATCH_INTERVAL_S
        try:
            job = await Job.get_by_id(job_id)
            if job is None or job.status != STATUS_RUNNING:
                # Cancelled, retried, or deleted out from under the run: whoever
                # did that owns the row's status now.
                return
            message = await ChatMessage.get_by_id(message_id)
        except Exception:
            # Transient (a locked DB, say): keep watching. Genuinely broken reads
            # must not strand the row as `running` for ever, so they run out.
            read_failures += 1
            logger.exception(
                "Job %s: could not read its run's message (%d/%d)",
                job_id[:8],
                read_failures,
                _MAX_READ_FAILURES,
            )
            if read_failures >= _MAX_READ_FAILURES:
                await Job.update_status(
                    job_id, STATUS_FAILED, _now_ms(), last_error="could not read the run's state"
                )
                await _after_settle(job_id)
                return
            continue
        read_failures = 0
        if message is None:
            await Job.update_status(
                job_id, STATUS_FAILED, _now_ms(), last_error="run message disappeared"
            )
            await _after_settle(job_id)
            return
        if not message.done:
            continue

        error = (message.meta or {}).get("error")
        if error:
            await Job.update_status(
                job_id, STATUS_FAILED, _now_ms(), last_error=str(error or "run failed")[:4000]
            )
            logger.info("Job %s run failed: %s", job_id[:8], str(error)[:200])
        else:
            await Job.update_status(job_id, STATUS_NEEDS_REVIEW, _now_ms(), last_error=None)
            logger.info("Job %s run finished → needs_review", job_id[:8])
        await _after_settle(job_id)
        return

    logger.warning("Job %s: run did not finish within %ss", job_id[:8], _RUN_TIMEOUT_S)
    await Job.update_status(
        job_id,
        STATUS_FAILED,
        _now_ms(),
        last_error=f"run did not finish within {_RUN_TIMEOUT_S}s",
    )
    await _after_settle(job_id)


async def run_job(app, job: Job) -> None:
    """Run one claimed job. Failures land on the row, never in the loop."""
    from cptr.utils.chat_task import start_task
    from cptr.utils.identity import internal_request_for_user
    from cptr.socket.main import emit_to_user

    if job.executor == EXECUTOR_HUMAN or job.kind == KIND_NOTE:
        # Nothing for a model to do; put it back instead of pretending to run it.
        await Job.update_status(job.id, STATUS_OPEN, _now_ms())
        return

    try:
        chat, assistant_msg, target, error = await _prepare_run(app, job)
    except Exception as exc:
        logger.exception("Job %s: could not be launched", job.id[:8])
        await Job.update_status(job.id, STATUS_FAILED, _now_ms(), last_error=str(exc)[:4000])
        return

    if not chat or not assistant_msg:
        if error == _BUSY:
            # The chat is mid-turn. Nothing is wrong with the job; hand it back
            # to the queue and try again next poll (timer semantics).
            logger.info("Job %s: %s, requeued", job.id[:8], _BUSY)
            await Job.update_status(job.id, STATUS_QUEUED, _now_ms())
            return
        logger.info("Job %s not launched: %s", job.id[:8], error)
        await Job.update_status(
            job.id, STATUS_FAILED, _now_ms(), last_error=error or "could not start the run"
        )
        return

    request = await internal_request_for_user(app, job.user_id)
    await emit_to_user(
        job.user_id,
        {
            "chat_id": chat.id,
            "message_id": assistant_msg.id,
            "pending_inputs_processed": True,
        },
    )

    # Remember where the run lives, so /cancel can reach it and a human can
    # find it from the job alone.
    await Job.update_by_id(
        job.id,
        meta={
            **(job.meta or {}),
            "run_chat_id": chat.id,
            "run_message_id": assistant_msg.id,
            "run_started_at": _now_ms(),
        },
    )

    # A human can cancel in the gap between the row claiming `running` and the
    # turn actually starting: `_prepare_run` writes the status, `start_task`
    # registers the task, and `/cancel` can only reach tasks that already exist.
    # Without this re-read the cancel is accepted (the row reads `cancelled`) and
    # the run happens anyway. As late as possible — the leftover window is the
    # microseconds between this read and the registry write.
    current = await Job.get_by_id(job.id)
    if current is None or current.status != STATUS_RUNNING:
        from cptr.models import ChatMessage

        await ChatMessage.update(assistant_msg.id, content="cancelled", done=True)
        logger.info(
            "Job %s: cancelled before its run started (status=%s)",
            job.id[:8],
            current.status if current else "gone",
        )
        return

    start_task(
        request,
        message_id=assistant_msg.id,
        chat_id=chat.id,
        user_id=job.user_id,
        workspace=job.workspace,
        target=target,
    )
    asyncio.create_task(_watch_run(job.id, assistant_msg.id))
    logger.info(
        "Job '%s' (%s) running in %s chat %s",
        job.title[:60],
        job.id[:8],
        "parent" if job.parent_chat else "a new",
        chat.id[:8],
    )


async def job_worker_loop(app) -> None:
    """Deprecated alias. The one scheduler is ``task_scheduler_loop``.

    Kept so an older ``app.py`` or a lane that has not been restarted cannot
    start a second, rival scheduler: this now *is* the task scheduler.
    """
    from cptr.utils.task_scheduler import task_scheduler_loop

    logger.warning("job_worker_loop is deprecated; running task_scheduler_loop")
    await task_scheduler_loop(app)


async def recover_jobs() -> None:
    """Settle rows left `running` by a crash or restart.

    An agent loop does not survive a process restart, so a `running` row is a
    run that is not coming back. Marking it `failed` keeps `needs_review`
    meaning "a human has something to look at".
    """
    for job in await Job.list_running():
        await Job.update_status(
            job.id, STATUS_FAILED, _now_ms(), last_error="interrupted by restart"
        )
        logger.warning("Job %s was interrupted by a restart", job.id[:8])
