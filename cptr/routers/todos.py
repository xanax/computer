"""Workspace todos router — now a shim over the `jobs` table.

Humans edit todos directly (list/add/toggle/remove). Chats propose mutations
via TodoRequest rows, which a human must approve or reject in the dashboard.

Phase 1 of ``notes/NOTES-myriad-job-queue.md``: a todo *is* a job with
``executor='human'`` and ``trigger='manual'`` (or ``'at'`` once it has been
deferred to a model). The routes and their response shapes are unchanged — the
dashboard cannot tell the difference. ``workspace_todos`` is still written in
lockstep by ``cptr/models/jobs.py``, so chat tools reading it keep working.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from cptr.models.jobs import Job, create_human_todo
from cptr.models.todos import TodoRequest
from cptr.utils.config import check_access, now_ms

router = APIRouter(prefix="/api/todos", tags=["todos"])

COOKIE_NAME = "cptr_session"

#: Todo-shaped jobs: human tasks and tasks deferred to a model. Recurring
#: (`rrule`) and non-actionable (`note`) rows belong to their own surfaces.
TODO_TRIGGERS = ("manual", "at")
TODO_KIND = "task"


def _get_user(request: Request) -> str:
    token = request.cookies.get(COOKIE_NAME)
    client_host = request.client.host if request.client else "127.0.0.1"
    auth = check_access(client_host=client_host, jwt_token=token)
    if not auth or not auth.user_id:
        raise HTTPException(401, "authentication required")
    return auth.user_id


def _todo_dict(j: Job) -> dict:
    """The pre-jobs todo response shape, unchanged.

    `status` collapses back to the two values the dashboard knows: anything not
    finished reads as ``open`` (a queued or awaiting-review deferral is still an
    outstanding task).
    """
    return {
        "id": j.id,
        "workspace": j.workspace,
        "title": j.title,
        "status": "done" if j.status == "done" else "open",
        "source": j.source,
        "created_at": j.created_at,
        "updated_at": j.updated_at,
    }


def _request_dict(r: TodoRequest) -> dict:
    return {
        "id": r.id,
        "workspace": r.workspace,
        "action": r.action,
        "todo_id": r.todo_id,
        "title": r.title,
        "status": r.status,
        "created_at": r.created_at,
        "resolved_at": r.resolved_at,
    }


async def _notify_changed(user_id: str, workspace: str) -> None:
    from cptr.socket.main import emit_jobs_changed

    await emit_jobs_changed(user_id, workspace)


async def _list_todo_jobs(user_id: str, workspace: str) -> list[Job]:
    jobs = await Job.list_for_workspace(user_id, workspace)
    return [
        j for j in jobs if j.kind == TODO_KIND and j.trigger in TODO_TRIGGERS
    ]


async def _get_todo(todo_id: str, user_id: str) -> Job:
    job = await Job.get_by_id(todo_id)
    if not job or job.user_id != user_id or job.kind != TODO_KIND:
        raise HTTPException(404, "todo not found")
    return job


# ── List todos + pending requests ───────────────────────────


@router.get("")
async def list_todos(
    request: Request,
    workspace: str = Query(..., description="Workspace path"),
):
    user_id = _get_user(request)
    todos = await _list_todo_jobs(user_id, workspace)
    pending = await TodoRequest.list_pending(user_id, workspace)
    return {
        "todos": [_todo_dict(t) for t in todos],
        "pending_requests": [_request_dict(r) for r in pending],
    }


# ── Human: add directly ─────────────────────────────────────


class AddTodoRequest(BaseModel):
    workspace: str
    title: str


@router.post("")
async def add_todo(request: Request, body: AddTodoRequest):
    user_id = _get_user(request)
    title = body.title.strip()
    if not title:
        raise HTTPException(400, "title is required")

    todo = await create_human_todo(
        user_id=user_id,
        workspace=body.workspace,
        title=title,
        source="human",
        created_at=now_ms(),
    )
    await _notify_changed(user_id, body.workspace)
    return _todo_dict(todo)


# ── Human: toggle open/done ─────────────────────────────────


@router.post("/{todo_id}/toggle")
async def toggle_todo(request: Request, todo_id: str):
    user_id = _get_user(request)
    todo = await _get_todo(todo_id, user_id)

    new_status = "done" if todo.status == "open" else "open"
    fields: dict = {}
    if new_status == "done" and todo.trigger == "at":
        # Finishing it by hand cancels the deferred run: a model should not be
        # woken to do work the human just did. The deferral is kept in `meta`
        # for the record.
        fields = {
            "executor": "human",
            "trigger": "manual",
            "trigger_at": None,
            "meta": {**(todo.meta or {}), "defer_cancelled_at": now_ms()},
        }

    await Job.update_status(todo_id, new_status, now_ms(), **fields)
    await _notify_changed(user_id, todo.workspace)

    updated = await Job.get_by_id(todo_id)
    return _todo_dict(updated)


# ── Human: remove directly ──────────────────────────────────


@router.delete("/{todo_id}")
async def remove_todo(request: Request, todo_id: str):
    user_id = _get_user(request)
    todo = await _get_todo(todo_id, user_id)

    await Job.delete(todo_id)
    await _notify_changed(user_id, todo.workspace)
    return {"ok": True}


# ── Verify (approve / reject) a chat-proposed mutation ──────


@router.post("/requests/{request_id}/approve")
async def approve_request(request: Request, request_id: str):
    user_id = _get_user(request)
    req = await TodoRequest.get_by_id(request_id)
    if not req or req.user_id != user_id:
        raise HTTPException(404, "request not found")
    if req.status != "pending":
        raise HTTPException(409, "request already resolved")

    now = now_ms()

    if req.action == "add":
        title = (req.title or "").strip()
        if not title:
            raise HTTPException(400, "request has no title")
        await create_human_todo(
            user_id=user_id,
            workspace=req.workspace,
            title=title,
            source="chat",
            created_at=now,
        )
    elif req.action == "complete":
        if not req.todo_id:
            raise HTTPException(400, "request has no todo_id")
        await Job.update_status(req.todo_id, "done", now)
    elif req.action == "reopen":
        if not req.todo_id:
            raise HTTPException(400, "request has no todo_id")
        await Job.update_status(req.todo_id, "open", now)
    elif req.action == "remove":
        if not req.todo_id:
            raise HTTPException(400, "request has no todo_id")
        await Job.delete(req.todo_id)
    else:
        raise HTTPException(400, f"unknown action: {req.action}")

    await TodoRequest.resolve(request_id, "approved", now)
    await _notify_changed(user_id, req.workspace)
    return {"ok": True}


@router.post("/requests/{request_id}/reject")
async def reject_request(request: Request, request_id: str):
    user_id = _get_user(request)
    req = await TodoRequest.get_by_id(request_id)
    if not req or req.user_id != user_id:
        raise HTTPException(404, "request not found")
    if req.status != "pending":
        raise HTTPException(409, "request already resolved")

    await TodoRequest.resolve(request_id, "rejected", now_ms())
    await _notify_changed(user_id, req.workspace)
    return {"ok": True}
