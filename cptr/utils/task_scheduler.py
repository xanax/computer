"""The one scheduler: recurring templates → run rows → the queue.

Phase 4 of ``notes/NOTES-myriad-job-queue.md``; storage in migration ``0010``.

Before this, three loops ticked on their own clocks and could not see each
other's work:

- ``automation_worker_loop`` fired ``automations`` rows into fresh chats;
- ``timer_worker_loop`` polled dormant child chats at ``meta.timer_at``; a
  survivor of that era is folded into this queue at boot by
  ``timers.fold_legacy_timers``;
- ``job_worker_loop`` fired deferred todos.

They are now one loop over one table. Each phase of a tick is a single
conditional SQL statement, so two processes (or two lanes of the same server)
can run this concurrently and still fire each occurrence exactly once:

1. ``promote_due_templates`` — a recurring task falls due; spawn a
   ``kind='run'`` child and advance the template's ``trigger_at``. The claim is
   an ``UPDATE … WHERE trigger_at = <the value we read>``, so the loser of a
   race updates nothing and spawns nothing.
2. ``mark_due_queued`` — one-shot rows (deferred tasks, timers) cross their time
   and go ``open → queued``.
3. ``claim_due`` — whoever flips ``queued → running`` owns the run.

Execution itself is ``cptr.utils.jobs.run_job``, unchanged: it already handles
both destinations (wake a parent chat, or start a fresh one) and both failure
modes (a busy chat requeues, a broken one fails).

Environment:
    JOB_POLL_INTERVAL – seconds between ticks (default: 2)
"""

from __future__ import annotations

import asyncio
import logging
import time

from cptr.env import JOB_POLL_INTERVAL
from cptr.models.jobs import (
    EXECUTOR_HUMAN,
    KIND_RUN,
    STATUS_OPEN,
    STATUS_QUEUED,
    TRIGGER_AT,
    TRIGGER_RRULE,
    Job,
)

logger = logging.getLogger(__name__)

#: Rows a single tick will promote/claim, so one huge backlog cannot starve the
#: loop. The next tick picks up where this one stopped.
_TICK_LIMIT = 10


def _now_ms() -> int:
    return int(time.time() * 1000)


async def tick(app) -> dict[str, int]:
    """Run one scheduling pass. Returns what it did, for tests and probes."""
    now_ns = time.time_ns()

    spawned = await Job.promote_due_templates(now_ns, limit=_TICK_LIMIT)
    for run in spawned:
        logger.info(
            "Recurring task '%s' fell due → run %s", run.title[:60], run.id[:8]
        )
        await sync_legacy_template(run)

    fired = await Job.mark_due_queued(now_ns, limit=_TICK_LIMIT)
    if fired:
        logger.info("Fired %d due task(s)", len(fired))

    claimed = await Job.claim_due(limit=_TICK_LIMIT)
    if claimed:
        from cptr.utils.jobs import run_job

        for job in claimed:
            asyncio.create_task(run_job(app, job))

    return {
        "promoted": len(spawned),
        "fired": len(fired),
        "claimed": len(claimed),
    }


async def task_scheduler_loop(app) -> None:
    """Poll for work. Every failure is logged and the loop continues."""
    logger.info("Task scheduler started (poll interval: %ss)", JOB_POLL_INTERVAL)
    while True:
        try:
            await tick(app)
        except Exception:
            logger.exception("Task scheduler error")
        await asyncio.sleep(JOB_POLL_INTERVAL)


async def run_task_now(app, job: Job, *, webhook_payload: str | None = None) -> Job:
    """Queue one run of ``job`` immediately, off its schedule.

    "Run now" and a webhook call are the same request — do it once, now — so
    they land in the same place: a ``kind='run'`` child, exactly like a firing
    that fell due. The tick after this claims it, so there is still one code
    path that executes anything.

    A run queued this way carries a payload, because a webhook's body is not
    reproducible: it goes in ``meta.webhook_payload`` for ``build_prompt``.
    """
    if job.executor == EXECUTOR_HUMAN:
        raise ValueError("a human task has nothing to run")

    now = _now_ms()
    run = await Job.create(
        user_id=job.user_id,
        workspace=job.workspace,
        title=job.title,
        kind=KIND_RUN,
        executor=job.executor,
        trigger=TRIGGER_AT,
        status=STATUS_QUEUED,
        resource=job.resource,
        payload=job.payload,
        priority=job.priority,
        parent_chat=job.parent_chat,
        parent_job=job.id,
        source=job.source,
        origin_chat=job.origin_chat,
        meta={
            "manual": True,
            "fired_at": now,
            **({"webhook_payload": webhook_payload} if webhook_payload else {}),
        },
        created_at=now,
    )
    await sync_legacy_template(run)

    from cptr.socket.main import emit_jobs_changed

    await emit_jobs_changed(job.user_id, job.workspace)
    return run


# ── Cancellation ─────────────────────────────────────────────
# `meta.cancel_on` is what makes a timer a timer: "remind me in an hour, unless
# I read the chat first". It survives a restart because it lives on the row
# rather than in a pending coroutine.

async def cancel_due_jobs_for_event(event) -> None:
    """Cancel pending `trigger='at'` jobs whose parent chat saw ``event``."""
    subject = event.subject or {}
    if subject.get("type") != "chat" or not subject.get("id"):
        return

    chat_id = str(subject["id"])
    cancelled = await Job.cancel_cancellable(chat_id, event.event)
    if not cancelled:
        return

    from cptr.socket.main import emit_jobs_changed

    logger.info(
        "Cancelled %d timer job(s) for %s on %s", len(cancelled), chat_id[:8], event.event
    )
    for job in cancelled:
        await emit_jobs_changed(job.user_id, job.workspace)


# ── Legacy mirror ────────────────────────────────────────────
# `automations`/`automation_runs` stay populated for one release so a rollback
# lands on a working table (design note §8.4). Only rows that already existed in
# the legacy table are written back — a task created in the new UI has no
# legacy row and is not invented into one.

async def sync_legacy_template(run: Job) -> None:
    """Stamp ``last_run_at``/``next_run_at`` and open a run record, if legacy."""
    from cptr.models.automations import Automation, AutomationRun

    if not run.parent_job:
        return
    template = await Job.get_by_id(run.parent_job)
    if template is None:
        return
    legacy = await Automation.get_by_id(template.id)
    if legacy is None:
        return

    try:
        await Automation.update_by_id(
            template.id,
            updated_at=_now_ms(),
            is_active=template.status == STATUS_OPEN,
            next_run_at=template.trigger_at,
            last_run_at=time.time_ns(),
        )
        await AutomationRun.create(
            automation_id=template.id,
            status="running",
            chat_id=None,
            error=None,
            created_at=_now_ms(),
            run_id=run.id,
        )
    except Exception:
        logger.warning("Could not mirror run %s to automations", run.id[:8], exc_info=True)


async def sync_legacy_toggle(task: Job) -> None:
    """Mirror a recurring task's paused/resumed state into ``automations``.

    ``is_active`` is how the legacy table spells "will fire again", so a paused
    task must clear it: were it left true, a rollback would start firing a task
    the human had switched off.
    """
    from cptr.models.automations import Automation

    if task.trigger != TRIGGER_RRULE:
        return
    if await Automation.get_by_id(task.id) is None:
        return

    try:
        await Automation.update_by_id(
            task.id,
            updated_at=_now_ms(),
            is_active=task.status == STATUS_OPEN,
            next_run_at=task.trigger_at,
        )
    except Exception:
        logger.warning("Could not mirror toggle of task %s", task.id[:8], exc_info=True)


async def sync_legacy_run_status(job_id: str) -> None:
    """Close the legacy run record once the job's own run settles."""
    from cptr.models.jobs import STATUS_FAILED, STATUS_NEEDS_REVIEW
    from cptr.models.automations import Automation, AutomationRun

    job = await Job.get_by_id(job_id)
    if job is None or job.kind != KIND_RUN or not job.parent_job:
        return
    if await Automation.get_by_id(job.parent_job) is None:
        return

    if job.status == STATUS_NEEDS_REVIEW or job.status == STATUS_OPEN:
        # Old automations had no review step: a run that produced a reply
        # succeeded. `needs_review` is this scheme's version of that.
        status, error = "success", None
    elif job.status == STATUS_FAILED:
        status, error = "error", job.last_error
    else:
        return

    try:
        await AutomationRun.settle(job.id, status, chat_id=(job.meta or {}).get("run_chat_id"), error=error)
    except Exception:
        logger.warning("Could not close legacy run %s", job.id[:8], exc_info=True)


# ── Recovery ─────────────────────────────────────────────────

async def recover_tasks() -> None:
    """Settle rows a restart interrupted, and requeue ones it never claimed.

    ``Job.list_running`` rows are runs whose agent loop died with the process:
    they are marked failed so `needs_review` keeps meaning "a human has
    something to look at". Queued rows are left alone — they are still wanted,
    and the next tick picks them up.
    """
    from cptr.utils.jobs import recover_jobs

    await recover_jobs()

    overdue = await Job.count_overdue_templates(time.time_ns())
    if overdue:
        logger.info(
            "%d recurring task(s) fell due while the server was down; "
            "the next tick fires the latest occurrence of each",
            overdue,
        )
