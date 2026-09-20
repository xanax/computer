"""Workspace job board API.

Phase 2 of ``notes/NOTES-myriad-job-queue.md``: read the whole board (not just
the human slice `/api/todos` shows), inspect how a run landed, defer a job to a
model, and cancel one.

Automation-backed jobs do not exist yet (Phase 4), so in practice this lists
human todos plus anything deferred to a model.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from cptr.env import JOB_TOOL_APPROVAL_MODE
from cptr.models.jobs import OPEN_STATUSES, Job
from cptr.utils.config import check_access, now_ms

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

COOKIE_NAME = "cptr_session"


def _get_user(request: Request) -> str:
    token = request.cookies.get(COOKIE_NAME)
    client_host = request.client.host if request.client else "127.0.0.1"
    auth = check_access(client_host=client_host, jwt_token=token)
    if not auth or not auth.user_id:
        raise HTTPException(401, "authentication required")
    return auth.user_id


def _job_dict(j: Job) -> dict:
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
        "source": j.source,
        "origin_chat": j.origin_chat,
        "payload": j.payload,
        "meta": j.meta or {},
        "created_at": j.created_at,
        "updated_at": j.updated_at,
    }


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
    workspace: str = Query(..., description="Workspace path"),
    status: str = "",
    limit: int = 0,
):
    """List a workspace's jobs, oldest first. ``limit=0`` means no limit."""
    user_id = _get_user(request)
    statuses = tuple(s.strip() for s in str(status).split(",") if s.strip()) or None
    jobs = await Job.list_for_workspace(
        user_id, workspace, statuses=statuses, limit=limit or None
    )
    return {
        "jobs": [_job_dict(j) for j in jobs],
        "counts": await Job.count_by_status(user_id, workspace),
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
        mirror_legacy=body.kind == "task",
        meta={"legacy": "workspace_todos"} if body.kind == "task" else None,
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
    for field in ("title", "status", "priority", "executor", "trigger", "rrule", "payload", "parent_chat", "resource"):
        if field in provided:
            values[field] = provided[field]
    if values.get("status") in ("queued", "running"):
        raise HTTPException(400, "queued/running are the scheduler's to set")
    if "title" in values and not (values["title"] or "").strip():
        raise HTTPException(400, "title cannot be empty")

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


# ── Board metadata ──────────────────────────────────────────


@router.get("/meta/config")
async def job_config(request: Request):
    """What the board would assume when it runs a job with no chat of its own."""
    _get_user(request)
    return {
        "tool_approval_mode": JOB_TOOL_APPROVAL_MODE,
        "claimable_statuses": list(OPEN_STATUSES),
    }
