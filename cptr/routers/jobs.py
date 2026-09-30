"""Task board API (`/api/jobs`).

Phase 2 of ``notes/NOTES-myriad-job-queue.md``, widened in Phase 4: this is now
the read for *every* task, from a plain human todo to a recurring task's run
history, in one workspace or across all of them.

The store is one table (`jobs`), so the difference between the workspace
dashboard and the Tasks tab is one query parameter, not one code path:

- ``GET /api/jobs?workspace=<path>`` — the workspace dashboard. Everything in
  that workspace: its todos, its deferred items, its schedules.
- ``GET /api/jobs`` (no workspace) — the Tasks tab. The same rows, every
  workspace, grouped client-side by ``workspace``.

``/api/todos`` remains as the human slice of the same table, for the chat tools
and anything that has not moved yet.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from cptr.env import JOB_TOOL_APPROVAL_MODE
from cptr.models.jobs import (
    KIND_RUN,
    OPEN_STATUSES,
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_NEEDS_REVIEW,
    STATUS_OPEN,
    STATUS_PAUSED,
    Job,
)
from cptr.utils.config import check_access, now_ms

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

COOKIE_NAME = "cptr_session"


#: A recurring task is `open`; switching it off makes it `paused`. These are the
#: two states `POST /{id}/toggle` moves between.
LIVE_STATUSES = ("open", STATUS_PAUSED)


def _get_user(request: Request) -> str:
    token = request.cookies.get(COOKIE_NAME)
    client_host = request.client.host if request.client else "127.0.0.1"
    auth = check_access(client_host=client_host, jwt_token=token)
    if not auth or not auth.user_id:
        raise HTTPException(401, "authentication required")
    return auth.user_id


def _job_dict(j: Job, last_run: Job | None = None) -> dict:
    return {
        "id": j.id,
        "workspace": j.workspace,
        "title": j.title,
        "kind": j.kind,
        "executor": j.executor,
        "trigger": j.trigger,
        "trigger_at": j.trigger_at,
        "rrule": j.rrule,
        "status": j.status,
        "priority": j.priority,
        "attempts": j.attempts,
        "last_error": j.last_error,
        "parent_chat": j.parent_chat,
        "parent_job": j.parent_job,
        "source": j.source,
        "origin_chat": j.origin_chat,
        "payload": j.payload,
        "meta": j.meta or {},
        "created_at": j.created_at,
        "updated_at": j.updated_at,
        # The newest firing of a recurring task, so a row can say "last run
        # failed" without a second request per row.
        "last_run": _run_summary(last_run) if last_run else None,
    }


def _run_summary(run: Job) -> dict:
    return {
        "id": run.id,
        "status": run.status,
        "created_at": run.created_at,
        "last_error": run.last_error,
        "chat_id": (run.meta or {}).get("run_chat_id"),
    }


async def _latest_runs(jobs: list[Job]) -> dict[str, Job]:
    """`{template_id: newest run}` for the given tasks, in one query."""
    from sqlalchemy import select

    from cptr.utils.db import get_db

    ids = [j.id for j in jobs if j.trigger == "rrule"]
    if not ids:
        return {}
    async with await get_db() as db:
        result = await db.execute(
            select(Job)
            .where(Job.parent_job.in_(ids))
            .order_by(Job.created_at.desc())
        )
        newest: dict[str, Job] = {}
        for run in result.scalars().all():
            newest.setdefault(run.parent_job, run)
    return newest


async def _notify_changed(user_id: str, workspace: str) -> None:
    from cptr.socket.main import emit_jobs_changed

    await emit_jobs_changed(user_id, workspace)


async def _get_job(job_id: str, user_id: str) -> Job:
    job = await Job.get_by_id(job_id)
    if not job or job.user_id != user_id:
        raise HTTPException(404, "job not found")
    return job


# ── Read ────────────────────────────────────────────────────


@router.get("")
async def list_jobs(
    request: Request,
    workspace: str = Query("", description="Workspace path (omit for all)"),
    status: str = "",
    kind: str = "",
    limit: int = 0,
):
    """List tasks, oldest first. ``limit=0`` means no limit.

    Omitting ``workspace`` is not an "admin" view: it is the same user's tasks,
    every workspace, which is what the Tasks tab needs. ``workspaces`` carries
    the per-workspace counts so that view can group and collapse without a
    request per workspace.
    """
    user_id = _get_user(request)
    statuses = tuple(s.strip() for s in str(status).split(",") if s.strip()) or None
    kinds = tuple(s.strip() for s in str(kind).split(",") if s.strip()) or None
    jobs = await Job.list_tasks(
        user_id, workspace or None, statuses=statuses, kinds=kinds, limit=limit or None
    )
    last_runs = await _latest_runs(jobs)
    return {
        "jobs": [_job_dict(j, last_runs.get(j.id)) for j in jobs],
        "counts": await Job.count_by_status(user_id, workspace or None),
        "workspaces": await Job.workspace_counts(user_id),
        "open_statuses": list(OPEN_STATUSES),
    }


@router.get("/{job_id}")
async def get_job(request: Request, job_id: str):
    user_id = _get_user(request)
    return _job_dict(await _get_job(job_id, user_id))


# ── Write ───────────────────────────────────────────────────


class CreateJobRequest(BaseModel):
    workspace: str
    title: str
    kind: str = "task"  # 'task' | 'note'
    executor: str = "human"  # 'human' | <model_id>
    trigger: str = "manual"  # 'manual' | 'at' | 'rrule'
    at: str | None = None  # for trigger='at'
    rrule: str | None = None  # for trigger='rrule'
    payload: str | None = None
    priority: int = 0
    parent_chat: str | None = None


@router.post("")
async def create_job(request: Request, body: CreateJobRequest):
    """Put a job on the board.

    Everything the board holds arrives this way, whatever its executor: a plain
    todo (``executor='human'``) is mirrored into ``workspace_todos`` so it shows
    up in ``/api/todos`` and in the built dashboard too.
    """
    user_id = _get_user(request)
    title = body.title.strip()
    if not title:
        raise HTTPException(400, "title is required")
    if body.kind not in ("task", "note"):
        raise HTTPException(400, f"unknown kind: {body.kind}")
    if body.trigger not in ("manual", "at", "rrule"):
        raise HTTPException(400, f"unknown trigger: {body.trigger}")
    if body.trigger == "window":
        raise HTTPException(400, "window triggers need price data (not built yet)")

    trigger_at = None
    if body.trigger == "at":
        if not body.at:
            raise HTTPException(400, "trigger='at' needs an `at` time")
        from cptr.utils.timers import parse_timer_at

        try:
            trigger_at = parse_timer_at(body.at)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    if body.trigger == "rrule" and not body.rrule:
        raise HTTPException(400, "trigger='rrule' needs an rrule")
    if body.trigger == "rrule":
        from cptr.utils.automations import next_run_ns, validate_rrule

        try:
            validate_rrule(body.rrule)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        # A recurring task must know when its first occurrence is, or the
        # scheduler has nothing to compare against.
        trigger_at = trigger_at or next_run_ns(body.rrule)

    job = await Job.create(
        user_id=user_id,
        workspace=body.workspace,
        title=title,
        kind=body.kind,
        executor=body.executor,
        trigger=body.trigger,
        trigger_at=trigger_at,
        rrule=body.rrule,
        payload=body.payload,
        priority=body.priority,
        parent_chat=body.parent_chat,
        source="human",
        # Only a plain human todo belongs in the legacy mirror: a schedule is
        # not a todo, and a run is not a task.
        mirror_legacy=body.kind == "task" and body.trigger in ("manual", "at"),
        meta={"legacy": "workspace_todos"}
        if (body.kind == "task" and body.trigger in ("manual", "at"))
        else None,
    )
    await _notify_changed(user_id, job.workspace)
    return _job_dict(job)


class PatchJobRequest(BaseModel):
    title: str | None = None
    status: str | None = None
    priority: int | None = None
    executor: str | None = None
    trigger: str | None = None
    at: str | None = None
    rrule: str | None = None
    payload: str | None = None
    parent_chat: str | None = None
    resource: str | None = None


@router.patch("/{job_id}")
async def patch_job(request: Request, job_id: str, body: PatchJobRequest):
    """Edit a job in place (the general form of ``/api/todos/{id}/toggle``)."""
    user_id = _get_user(request)
    await _get_job(job_id, user_id)

    provided = body.model_dump(exclude_unset=True)
    if not provided:
        raise HTTPException(400, "nothing to update")

    values: dict = {}
    if "at" in provided:
        if not body.at:
            raise HTTPException(400, "`at` cannot be cleared; use trigger='manual'")
        from cptr.utils.timers import parse_timer_at

        try:
            values["trigger_at"] = parse_timer_at(body.at)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    if body.rrule:
        from cptr.utils.automations import next_run_ns, validate_rrule

        try:
            validate_rrule(body.rrule)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        # The schedule moved, so its next occurrence moved with it: keeping the
        # old `trigger_at` would fire the old rule one last time.
        if "rrule" in provided:
            values["trigger_at"] = next_run_ns(body.rrule)

    for field in ("title", "status", "priority", "executor", "trigger", "rrule", "payload", "parent_chat", "resource"):
        if field in provided:
            values[field] = provided[field]
    if values.get("status") in ("queued", "running"):
        raise HTTPException(400, "queued/running are the scheduler's to set")
    if "title" in values and not (values["title"] or "").strip():
        raise HTTPException(400, "title cannot be empty")
    if "status" in values and values["status"] not in (
        STATUS_OPEN,
        STATUS_PAUSED,
        STATUS_DONE,
        STATUS_CANCELLED,
    ):
        raise HTTPException(400, f"a client cannot set status to {values['status']}")
    if "executor" in values and not values["executor"]:
        raise HTTPException(400, "executor cannot be empty")

    await Job.update_by_id(job_id, **values)
    job = await Job.get_by_id(job_id)
    await _notify_changed(user_id, job.workspace)
    return _job_dict(job)


@router.delete("/{job_id}")
async def delete_job(request: Request, job_id: str):
    """Remove a job (and its legacy todo mirror row)."""
    user_id = _get_user(request)
    job = await _get_job(job_id, user_id)
    await Job.delete(job_id)
    await _notify_changed(user_id, job.workspace)
    return {"ok": True}


# ── Defer a job to a model at a time ────────────────────────


class DeferJobRequest(BaseModel):
    at: str  # relative ("10m", "in 3 hours") or RFC 3339 with a timezone
    executor: str  # model id to run it
    parent_chat: str | None = None  # omit for a fresh chat
    payload: str | None = None  # extra instructions for the run


@router.post("/{job_id}/defer")
async def defer_job(request: Request, job_id: str, body: DeferJobRequest):
    """Schedule a job for later, run by a model on its own.

    The human keeps the row: ``executor`` becomes the model but the status
    stays ``open`` until the trigger fires. Finishing the todo by hand (see
    ``/api/todos/{id}/toggle``) cancels the deferral.
    """
    from cptr.utils.timers import parse_timer_at  # same "at" grammar as timers

    user_id = _get_user(request)
    job = await _get_job(job_id, user_id)
    if job.status not in ("open", "cancelled", "failed"):
        raise HTTPException(409, f"cannot defer a {job.status} job")

    try:
        trigger_at = parse_timer_at(body.at)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    await Job.update_by_id(
        job_id,
        executor=body.executor,
        trigger="at",
        trigger_at=trigger_at,
        payload=body.payload or job.payload,
        parent_chat=body.parent_chat,
        status="open",
        last_error=None,
        meta={**(job.meta or {}), "deferred_by": "api", "deferred_at": now_ms()},
    )
    await _notify_changed(user_id, job.workspace)
    return _job_dict(await Job.get_by_id(job_id))


# ── Cancel ──────────────────────────────────────────────────


@router.post("/{job_id}/cancel")
async def cancel_job(request: Request, job_id: str):
    """Stop a job: clear any pending trigger and kill a run in flight."""
    user_id = _get_user(request)
    job = await _get_job(job_id, user_id)
    if job.status in ("done", "cancelled"):
        raise HTTPException(409, f"job is already {job.status}")

    run_message_id = (job.meta or {}).get("run_message_id")
    if job.status == "running" and run_message_id:
        from cptr.utils.chat_task import cancel_task, is_running

        if is_running(run_message_id):
            await cancel_task(run_message_id)

    await Job.update_status(
        job_id,
        "cancelled",
        now_ms(),
        trigger="manual",
        trigger_at=None,
        last_error="cancelled by user",
    )
    await _notify_changed(user_id, job.workspace)
    return _job_dict(await Job.get_by_id(job_id))


# ── Schedule: run / pause / history / review ────────────────


@router.post("/{job_id}/run")
async def run_job_now(job_id: str, request: Request):
    """Do this task once, now, without touching its schedule.

    This is the same request a webhook makes, so it goes to the same place: a
    ``kind='run'`` child that the next tick claims. A recurring task keeps
    every future occurrence; a one-shot task keeps none.
    """
    from cptr.utils.task_scheduler import run_task_now

    user_id = _get_user(request)
    job = await _get_job(job_id, user_id)
    if job.kind == KIND_RUN:
        raise HTTPException(400, "a run cannot be run")
    try:
        run = await run_task_now(request.app, job)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return _job_dict(run)


@router.post("/{job_id}/toggle")
async def toggle_job(request: Request, job_id: str):
    """Pause or resume a task. Paused keeps its schedule and its history."""
    user_id = _get_user(request)
    job = await _get_job(job_id, user_id)
    if job.trigger != "rrule":
        raise HTTPException(400, "only a recurring task can be paused")
    if job.status not in LIVE_STATUSES:
        raise HTTPException(409, f"cannot pause a {job.status} task")

    resumed = job.status == STATUS_PAUSED
    values: dict = {"status": STATUS_OPEN if resumed else STATUS_PAUSED}
    if resumed:
        from cptr.utils.automations import next_run_ns

        # Resuming picks up the *next* occurrence: firing every hour it spent
        # paused would be a backlog, not a schedule.
        values["trigger_at"] = next_run_ns(job.rrule or "")
    await Job.update_by_id(job_id, **values)

    # Keep the legacy row honest for the one release both exist (0010).
    from cptr.utils.task_scheduler import sync_legacy_toggle

    await sync_legacy_toggle(await Job.get_by_id(job_id))
    await _notify_changed(user_id, job.workspace)
    return _job_dict(await Job.get_by_id(job_id))


@router.get("/{job_id}/runs")
async def list_job_runs(job_id: str, request: Request, limit: int = 20):
    """Every firing of a recurring task, newest first."""
    user_id = _get_user(request)
    job = await _get_job(job_id, user_id)
    runs = await Job.list_runs(job.id, limit=limit)
    return {
        "runs": [{**_job_dict(r), **_run_summary(r)} for r in runs],
        "task": _job_dict(job),
    }


class ReviewRequest(BaseModel):
    #: 'done' accepts the run's work; 'open' puts the task back on the board.
    action: str = "done"
    note: str | None = None


@router.post("/{job_id}/review")
async def review_job(request: Request, job_id: str, body: ReviewRequest):
    """A human settles a row: approve a finished run, or reopen the task.

    This is the only way anything reaches ``done`` (design note §7 - agents
    land proposals, not writes). Approving a *run* records the verdict on the
    run; the recurring template stays open, so "the 09:00 brief was fine today"
    cannot cancel tomorrow's.
    """
    user_id = _get_user(request)
    job = await _get_job(job_id, user_id)
    if body.action not in ("done", "open"):
        raise HTTPException(400, "action must be 'done' or 'open'")
    if job.status in ("running", "queued"):
        raise HTTPException(409, f"cannot review a {job.status} task")

    if body.action == "done":
        await Job.update_status(
            job_id,
            STATUS_DONE,
            now_ms(),
            last_error=None,
            meta={**(job.meta or {}), "reviewed_at": now_ms(), "review_note": body.note},
        )
    else:
        await Job.update_status(
            job_id, STATUS_OPEN, now_ms(), last_error=None, attempts=0
        )

    if job.kind == KIND_RUN:
        from cptr.utils.task_scheduler import sync_legacy_run_status

        await sync_legacy_run_status(job_id)

    updated = await Job.get_by_id(job_id)
    await _notify_changed(user_id, job.workspace)
    return _job_dict(updated)


class SnoozeRequest(BaseModel):
    #: Relative ("10m") or RFC 3339 with a timezone.
    at: str


@router.post("/{job_id}/snooze")
async def snooze_job(request: Request, job_id: str, body: SnoozeRequest):
    """Push a task's next due time out, keeping everything else."""
    from cptr.utils.timers import parse_timer_at

    user_id = _get_user(request)
    job = await _get_job(job_id, user_id)
    if job.status in ("running", "done", "cancelled"):
        raise HTTPException(409, f"cannot snooze a {job.status} task")
    try:
        trigger_at = parse_timer_at(body.at)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    await Job.update_by_id(
        job_id,
        trigger_at=trigger_at,
        status=STATUS_OPEN if job.trigger == "at" else job.status,
        updated_at=now_ms(),
    )
    await _notify_changed(user_id, job.workspace)
    return _job_dict(await Job.get_by_id(job_id))


# ── Board metadata ──────────────────────────────────────────


@router.get("/meta/config")
async def job_config(request: Request):
    """What the board would assume when it runs a job with no chat of its own."""
    _get_user(request)
    return {
        "tool_approval_mode": JOB_TOOL_APPROVAL_MODE,
        "claimable_statuses": list(OPEN_STATUSES),
    }
