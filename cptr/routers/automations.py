"""Legacy `/api/automations` — a translation layer over the task board.

An automation is a `jobs` row with `trigger='rrule'` as of migration 0010, and
its id survives the move (which is why saved webhook URLs still resolve). This
router keeps the old URLs and the old JSON shape so nothing outside the app
breaks, but every read and write lands on the one table the one scheduler polls
(`cptr.utils.task_scheduler`).

Nothing in the frontend calls it any more — the routes are redirects to
`/scheduled` and the panel is gone — so this exists for bookmarks, webhooks and
any stale client. New work belongs in `cptr/routers/jobs.py`.

Webhook tokens live in the job's `meta.webhook_token` (SHA-256, as before).
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from cptr.models.jobs import (
    EXECUTOR_HUMAN,
    KIND_RUN,
    STATUS_FAILED,
    STATUS_NEEDS_REVIEW,
    STATUS_OPEN,
    STATUS_PAUSED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    TRIGGER_RRULE,
    Job,
)
from cptr.utils.automations import next_n_runs_ns, next_run_ns, validate_rrule
from cptr.utils.config import check_access, now_ms

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/automations", tags=["automations"])

COOKIE_NAME = "cptr_session"
PAGE_SIZE = 30


def _get_user(request: Request) -> str:
    """Extract user_id from cookie, raise 401 if not authenticated."""
    token = request.cookies.get(COOKIE_NAME)
    client_host = request.client.host if request.client else "127.0.0.1"
    auth = check_access(client_host=client_host, jwt_token=token)
    if not auth or not auth.user_id:
        raise HTTPException(401, "authentication required")
    return auth.user_id


def _hash_token(token: str) -> str:
    """SHA-256 hash a webhook token."""
    return hashlib.sha256(token.encode()).hexdigest()


# ── Serialization: a job, in the shape this API always returned ──


def _run_status(job: Job) -> str:
    """Old run vocabulary: `running` | `success` | `error`."""
    if job.status == STATUS_FAILED:
        return "error"
    if job.status in (STATUS_NEEDS_REVIEW, STATUS_OPEN):
        # The old scheme had no review step: a run that produced a reply passed.
        return "success"
    if job.status in (STATUS_QUEUED, STATUS_RUNNING):
        return "running"
    return "success"


def _run_dict(run: Job) -> dict:
    return {
        "id": run.id,
        "automation_id": run.parent_job,
        "chat_id": (run.meta or {}).get("run_chat_id") or run.parent_chat,
        "status": _run_status(run),
        "error": run.last_error,
        "created_at": run.created_at,
    }


def _automation_dict(
    job: Job,
    last_run: Job | None = None,
    next_runs: list[int] | None = None,
    webhook_url: str | None = None,
) -> dict:
    """Serialize a recurring job the way an Automation used to be serialized."""
    meta = job.meta or {}
    return {
        "id": job.id,
        "user_id": job.user_id,
        "name": job.title,
        "prompt": job.payload or "",
        "model_id": "" if job.executor == EXECUTOR_HUMAN else job.executor,
        "workspace": job.workspace,
        "rrule": job.rrule,
        "is_active": job.status == STATUS_OPEN,
        "last_run_at": last_run.created_at if last_run else None,
        "next_run_at": job.trigger_at,
        "meta": job.meta,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "last_run": _run_dict(last_run) if last_run else None,
        "next_runs": next_runs,
        "has_webhook": bool(meta.get("webhook_token")),
        "webhook_url": webhook_url,
    }


async def _get_task(job_id: str, user_id: str) -> Job:
    job = await Job.get_by_id(job_id)
    if job is None or job.user_id != user_id or job.kind == KIND_RUN:
        raise HTTPException(404, "automation not found")
    return job


async def _last_run(job: Job) -> Job | None:
    runs = await Job.list_runs(job.id, limit=1)
    return runs[0] if runs else None


# ── List ────────────────────────────────────────────────────


@router.get("")
async def list_automations(
    request: Request,
    workspace: Optional[str] = Query(None, description="Workspace path (omit for all)"),
    query: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
):
    user_id = _get_user(request)

    statuses: list[str] | None = None
    if status == "active":
        statuses = [STATUS_OPEN]
    elif status == "paused":
        statuses = [STATUS_PAUSED]

    tasks = await Job.list_tasks(user_id, workspace, statuses=statuses, limit=None)
    tasks = [t for t in tasks if t.kind == "task"]
    if query:
        needle = query.lower()
        tasks = [t for t in tasks if needle in (t.title or "").lower()]

    total = len(tasks)
    skip = (page - 1) * PAGE_SIZE
    page_items = tasks[skip : skip + PAGE_SIZE]

    items = []
    for task in page_items:
        try:
            validate_rrule(task.rrule or "")
        except ValueError:
            # A rule that has run out (COUNT exhausted) is not an error here:
            # the row is simply finished. Serialize it without next_runs.
            items.append(_automation_dict(task, last_run=await _last_run(task)))
            continue
        items.append(
            _automation_dict(
                task,
                last_run=await _last_run(task),
                next_runs=next_n_runs_ns(task.rrule or "", 5),
            )
        )

    return {"items": items, "total": total}


# ── Create ──────────────────────────────────────────────────


class CreateAutomationRequest(BaseModel):
    name: str
    prompt: str
    model_id: str
    workspace: str
    rrule: str
    is_active: bool = True


@router.post("")
async def create_automation(request: Request, body: CreateAutomationRequest):
    user_id = _get_user(request)

    try:
        validate_rrule(body.rrule)
    except ValueError as e:
        raise HTTPException(400, str(e))

    job = await Job.create(
        user_id=user_id,
        workspace=body.workspace,
        title=body.name,
        executor=body.model_id or EXECUTOR_HUMAN,
        trigger=TRIGGER_RRULE,
        kind="task",
        status=STATUS_OPEN if body.is_active else STATUS_PAUSED,
        rrule=body.rrule,
        trigger_at=next_run_ns(body.rrule) if body.is_active else None,
        payload=body.prompt,
        created_at=now_ms(),
    )
    return _automation_dict(job, next_runs=next_n_runs_ns(body.rrule, 5))


# ── Read one ────────────────────────────────────────────────


@router.get("/{automation_id}")
async def get_automation(request: Request, automation_id: str):
    user_id = _get_user(request)
    job = await _get_task(automation_id, user_id)
    runs = next_n_runs_ns(job.rrule, 5) if job.rrule else None
    return _automation_dict(job, last_run=await _last_run(job), next_runs=runs)


# ── Update ──────────────────────────────────────────────────


class UpdateAutomationRequest(BaseModel):
    name: Optional[str] = None
    prompt: Optional[str] = None
    model_id: Optional[str] = None
    workspace: Optional[str] = None
    rrule: Optional[str] = None
    is_active: Optional[bool] = None


@router.post("/{automation_id}")
async def update_automation(request: Request, automation_id: str, body: UpdateAutomationRequest):
    user_id = _get_user(request)
    job = await _get_task(automation_id, user_id)

    values: dict = {}
    if body.name is not None:
        values["title"] = body.name
    if body.prompt is not None:
        values["payload"] = body.prompt
    if body.model_id is not None and body.model_id:
        values["executor"] = body.model_id
    if body.workspace is not None:
        values["workspace"] = body.workspace
    if body.rrule is not None:
        try:
            validate_rrule(body.rrule)
        except ValueError as e:
            raise HTTPException(400, str(e))
        values["rrule"] = body.rrule
        values["trigger_at"] = next_run_ns(body.rrule)
    if body.is_active is not None:
        values["status"] = STATUS_OPEN if body.is_active else STATUS_PAUSED
        if not body.is_active:
            values["trigger_at"] = None
        elif body.rrule is None and job.rrule:
            values["trigger_at"] = next_run_ns(job.rrule)

    if values:
        await Job.update_by_id(automation_id, **values)

    updated = await Job.get_by_id(automation_id)
    return _automation_dict(updated, next_runs=next_n_runs_ns(updated.rrule, 5))


# ── Pause / resume ──────────────────────────────────────────


@router.post("/{automation_id}/toggle")
async def toggle_automation(request: Request, automation_id: str):
    user_id = _get_user(request)
    job = await _get_task(automation_id, user_id)

    if job.status == STATUS_OPEN:
        await Job.update_status(automation_id, STATUS_PAUSED, trigger_at=None)
    else:
        nxt = next_run_ns(job.rrule) if job.rrule else None
        await Job.update_status(automation_id, STATUS_OPEN, trigger_at=nxt)

    toggled = await Job.get_by_id(automation_id)

    from cptr.utils.task_scheduler import sync_legacy_toggle

    await sync_legacy_toggle(toggled)
    return _automation_dict(toggled)


# ── Run now / webhook ───────────────────────────────────────


@router.post("/{automation_id}/run")
async def run_automation_now(
    automation_id: str,
    request: Request,
    token: Optional[str] = Query(None, description="Webhook token for unauthenticated access"),
):
    """Queue one run now. A webhook call is the same request, token instead of session."""
    from cptr.utils.task_scheduler import run_task_now

    if token:
        # Webhook path: validate the token, no session required.
        job = await Job.get_by_id(automation_id)
        if job is None:
            raise HTTPException(404, "automation not found")
        expected_hash = (job.meta or {}).get("webhook_token", "")
        if not expected_hash or _hash_token(token) != expected_hash:
            raise HTTPException(403, "invalid token")
        if job.status != STATUS_OPEN:
            raise HTTPException(409, "automation is paused")

        # A webhook body is not reproducible, so it rides along with the run.
        webhook_payload = None
        try:
            body = await request.json()
            if body:
                webhook_payload = json.dumps(body, indent=2)
        except Exception:
            pass
    else:
        user_id = _get_user(request)
        job = await _get_task(automation_id, user_id)
        webhook_payload = None

    if job.executor == EXECUTOR_HUMAN:
        raise HTTPException(400, "automation has no model to run")

    run = await run_task_now(request.app, job, webhook_payload=webhook_payload)
    logger.info("Queued run %s for '%s'", run.id[:8], (job.title or "")[:60])
    return _automation_dict(job, last_run=run, next_runs=next_n_runs_ns(job.rrule or "", 5))


# ── Delete ──────────────────────────────────────────────────


@router.delete("/{automation_id}")
async def delete_automation(request: Request, automation_id: str):
    user_id = _get_user(request)
    await _get_task(automation_id, user_id)
    await Job.delete(automation_id)
    return {"ok": True}


# ── Execution history ───────────────────────────────────────


@router.get("/{automation_id}/runs")
async def get_automation_runs(
    automation_id: str,
    request: Request,
    skip: int = 0,
    limit: int = 50,
):
    user_id = _get_user(request)
    await _get_task(automation_id, user_id)
    runs = await Job.list_runs(automation_id, limit=skip + limit)
    return [_run_dict(r) for r in runs[skip:]]


# ── Webhook management ──────────────────────────────────────


@router.post("/{automation_id}/webhook")
async def generate_webhook(request: Request, automation_id: str):
    """Generate or regenerate this task's webhook token.

    The plaintext URL is returned once; only its SHA-256 hash is stored.
    """
    user_id = _get_user(request)
    job = await _get_task(automation_id, user_id)

    plaintext = f"wh_{secrets.token_hex(20)}"
    meta = dict(job.meta or {})
    meta["webhook_token"] = _hash_token(plaintext)
    await Job.update_by_id(automation_id, meta=meta)

    base = str(request.base_url).rstrip("/")
    webhook_url = f"{base}/api/automations/{automation_id}/run?token={plaintext}"

    updated = await Job.get_by_id(automation_id)
    return _automation_dict(updated, webhook_url=webhook_url)


@router.delete("/{automation_id}/webhook")
async def revoke_webhook(request: Request, automation_id: str):
    """Revoke this task's webhook token."""
    user_id = _get_user(request)
    job = await _get_task(automation_id, user_id)

    meta = dict(job.meta or {})
    meta.pop("webhook_token", None)
    await Job.update_by_id(automation_id, meta=meta)

    updated = await Job.get_by_id(automation_id)
    return _automation_dict(updated)
