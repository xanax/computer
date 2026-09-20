"""Job: the one row type behind work in a workspace.

Phase 1+2 of ``notes/NOTES-myriad-job-queue.md``. A job answers three questions
through three columns: who runs it (``executor``), when it starts (``trigger``)
and what it may touch at the same time (``resource``). Human todos, one-shot
"defer this to a model at 03:00" timers and recurring automations are the same
row seen through different columns.

``workspace_todos`` is kept in lockstep (see the ``legacy mirror`` note below)
for one release so the dashboard, the chat tools and any rollback keep working.
"""

from __future__ import annotations

import time
import uuid

from sqlalchemy import BigInteger, Column, Index, Integer, Text, delete, func, select, update
from sqlalchemy.dialects.sqlite import JSON

from cptr.models.base import Base
from cptr.utils.db import get_db


def _uuid() -> str:
    return str(uuid.uuid4())


# ── Axes ─────────────────────────────────────────────────────

EXECUTOR_HUMAN = "human"

TRIGGER_MANUAL = "manual"
TRIGGER_AT = "at"
TRIGGER_RRULE = "rrule"
TRIGGER_WINDOW = "window"  # not implemented until Phase 3 (needs price data)

KIND_TASK = "task"
KIND_NOTE = "note"  # renders in the list, executes never

# ── Statuses (§3.2 of the design note) ───────────────────────

STATUS_OPEN = "open"  # visible, not yet runnable
STATUS_QUEUED = "queued"  # trigger fired, waiting for its lane
STATUS_RUNNING = "running"
STATUS_DONE = "done"  # only a human-sourced transition lands here
STATUS_NEEDS_REVIEW = "needs_review"  # an agent produced something for a human
STATUS_FAILED = "failed"  # carries last_error; attempts bounds retries
STATUS_BLOCKED = "blocked"  # depends_on is not finished
STATUS_CANCELLED = "cancelled"

#: Statuses a human can still act on (the dashboard's "outstanding" set).
OPEN_STATUSES = (STATUS_OPEN, STATUS_QUEUED, STATUS_RUNNING, STATUS_BLOCKED)
#: Terminal or human-facing statuses.
SETTLED_STATUSES = (STATUS_DONE, STATUS_NEEDS_REVIEW, STATUS_FAILED, STATUS_CANCELLED)


def _legacy_todo_status(status: str) -> str:
    """Map a job status onto the two-value `workspace_todos.status` vocab.

    Anything that is not finished reads as ``open``: a deferred run that is
    queued, running or awaiting review has not completed the task, and only
    ``done`` means a human decided it did (see §7 — agents land proposals, not
    writes).
    """
    return "done" if status == STATUS_DONE else "open"


class Job(Base):
    """A unit of workspace work: a human task, a deferred run, a schedule."""

    __tablename__ = "jobs"

    id = Column(Text, primary_key=True, default=_uuid)
    user_id = Column(Text, nullable=False)
    workspace = Column(Text, nullable=False)
    resource = Column(Text, nullable=False, default="")
    title = Column(Text, nullable=False)
    kind = Column(Text, nullable=False, default=KIND_TASK)
    executor = Column(Text, nullable=False)  # 'human' | <model_id>
    trigger = Column(Text, nullable=False)  # 'manual' | 'at' | 'rrule' | 'window'
    trigger_at = Column(BigInteger, nullable=True)  # ns, for 'at'
    rrule = Column(Text, nullable=True)
    deadline = Column(BigInteger, nullable=True)  # ns, for 'window'
    payload = Column(Text, nullable=True)  # prompt handed to the agent
    status = Column(Text, nullable=False, default=STATUS_OPEN)
    priority = Column(Integer, nullable=False, default=0)
    depends_on = Column(Text, nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    parent_chat = Column(Text, nullable=True)  # chat to wake; NULL = new chat
    source = Column(Text, nullable=False, default="human")  # 'human' | 'chat'
    origin_chat = Column(Text, nullable=True)  # chat that proposed it
    meta = Column(JSON, nullable=True)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index("ix_jobs_user_ws_status", "user_id", "workspace", "status"),
        Index("ix_jobs_due", "status", "trigger_at"),
        Index("ix_jobs_resource", "resource", "status", "priority", "created_at"),
    )

    # ── Reads ────────────────────────────────────────────────

    @staticmethod
    async def get_by_id(job_id: str) -> Job | None:
        async with await get_db() as db:
            result = await db.execute(select(Job).where(Job.id == job_id))
            return result.scalar_one_or_none()

    @staticmethod
    async def list_for_workspace(
        user_id: str,
        workspace: str,
        statuses: tuple[str, ...] | list[str] | None = None,
        limit: int | None = None,
    ) -> list[Job]:
        """Jobs in a workspace, oldest first (the dashboard's order)."""
        async with await get_db() as db:
            stmt = select(Job).where(Job.user_id == user_id, Job.workspace == workspace)
            if statuses:
                stmt = stmt.where(Job.status.in_(list(statuses)))
            stmt = stmt.order_by(Job.created_at.asc())
            if limit:
                stmt = stmt.limit(limit)
            result = await db.execute(stmt)
            return list(result.scalars().all())

    @staticmethod
    async def count_by_status(user_id: str, workspace: str) -> dict[str, int]:
        """`{status: count}` for a workspace."""
        async with await get_db() as db:
            result = await db.execute(
                select(Job.status, func.count())
                .where(Job.user_id == user_id, Job.workspace == workspace)
                .group_by(Job.status)
            )
            return {status: count for status, count in result.all()}

    # ── Writes ───────────────────────────────────────────────

    @staticmethod
    async def create(
        user_id: str,
        workspace: str,
        title: str,
        executor: str = EXECUTOR_HUMAN,
        trigger: str = TRIGGER_MANUAL,
        created_at: int | None = None,
        *,
        kind: str = KIND_TASK,
        status: str = STATUS_OPEN,
        resource: str = "",
        trigger_at: int | None = None,
        rrule: str | None = None,
        deadline: int | None = None,
        payload: str | None = None,
        priority: int = 0,
        depends_on: str | None = None,
        parent_chat: str | None = None,
        source: str = "human",
        origin_chat: str | None = None,
        meta: dict | None = None,
        job_id: str | None = None,
        mirror_legacy: bool = False,
    ) -> Job:
        """Create a job. ``mirror_legacy`` also writes the `workspace_todos` row."""
        now = created_at if created_at is not None else int(time.time() * 1000)
        async with await get_db() as db:
            job = Job(
                id=job_id or _uuid(),
                user_id=user_id,
                workspace=workspace,
                resource=resource,
                title=title,
                kind=kind,
                executor=executor,
                trigger=trigger,
                trigger_at=trigger_at,
                rrule=rrule,
                deadline=deadline,
                payload=payload,
                status=status,
                priority=priority,
                depends_on=depends_on,
                attempts=0,
                parent_chat=parent_chat,
                source=source,
                origin_chat=origin_chat,
                meta=meta,
                created_at=now,
                updated_at=now,
            )
            db.add(job)
            if mirror_legacy:
                await _mirror_upsert(db, job)
            await db.commit()
            await db.refresh(job)
            return job

    @staticmethod
    async def update_status(job_id: str, status: str, updated_at: int | None = None, **fields) -> bool:
        """Set a job's status (plus any extra columns) and mirror the legacy row."""
        values = {"status": status, "updated_at": updated_at or int(time.time() * 1000)}
        values.update(fields)
        return await Job.update_by_id(job_id, **values)

    @staticmethod
    async def update_by_id(job_id: str, **values) -> bool:
        """Patch a job in place. Mirrors status/title into `workspace_todos`."""
        if not values:
            return False
        values.setdefault("updated_at", int(time.time() * 1000))
        async with await get_db() as db:
            result = await db.execute(update(Job).where(Job.id == job_id).values(**values))
            if result.rowcount and ("status" in values or "title" in values):
                await _mirror_update(db, job_id, values)
            await db.commit()
            return result.rowcount > 0

    @staticmethod
    async def delete(job_id: str) -> bool:
        """Delete a job, its legacy mirror row, and any proposal about it.

        A chat's pending request targets this job, so deleting the job answers
        the question by itself — leaving the row pending would show an Approve
        button for a todo that no longer exists (B-013).
        """
        from cptr.models.todos import TodoRequest
        from cptr.utils.config import now_ms

        async with await get_db() as db:
            await _mirror_delete(db, job_id)
            result = await db.execute(delete(Job).where(Job.id == job_id))
            await db.commit()
        if result.rowcount > 0:
            # A pending "remove" is what the human just did; the rest are moot.
            await TodoRequest.resolve_for_todo(
                job_id, now_ms(), approved_actions=("remove",)
            )
        return result.rowcount > 0

    # ── The clock ────────────────────────────────────────────

    @staticmethod
    async def mark_due_queued(now_ns: int, limit: int = 100) -> list[str]:
        """Fire due `trigger='at'` rows: `open` → `queued`. Returns their ids.

        A single conditional UPDATE, so two schedulers (or two processes) can
        race this without double-firing: whoever flips the row claims it.
        """
        now = int(time.time() * 1000)
        async with await get_db() as db:
            due = (
                select(Job.id)
                .where(
                    Job.status == STATUS_OPEN,
                    Job.trigger == TRIGGER_AT,
                    Job.trigger_at.is_not(None),
                    Job.trigger_at <= now_ns,
                    Job.executor != EXECUTOR_HUMAN,
                )
                .order_by(Job.priority.desc(), Job.trigger_at.asc())
                .limit(limit)
            )
            result = await db.execute(
                update(Job)
                .where(Job.id.in_(due))
                .values(status=STATUS_QUEUED, updated_at=now)
                .returning(Job.id)
            )
            ids = [row[0] for row in result.all()]
            await db.commit()
            return ids

    @staticmethod
    async def claim_due(limit: int = 10) -> list[Job]:
        """Claim queued jobs: `queued` → `running`, bumping `attempts`.

        Unlike `Automation.claim_due` (select, mutate, commit) this is one
        conditional UPDATE ... RETURNING, so it is safe for more than one
        consumer. Do not hold a transaction across the job's run.
        """
        now = int(time.time() * 1000)
        async with await get_db() as db:
            claimable = (
                select(Job.id)
                .where(
                    Job.status == STATUS_QUEUED,
                    Job.executor != EXECUTOR_HUMAN,
                )
                .order_by(Job.priority.desc(), Job.trigger_at.asc(), Job.created_at.asc())
                .limit(limit)
            )
            result = await db.execute(
                update(Job)
                .where(Job.id.in_(claimable))
                .values(status=STATUS_RUNNING, attempts=Job.attempts + 1, updated_at=now)
                .returning(Job.id)
            )
            ids = [row[0] for row in result.all()]
            if not ids:
                await db.commit()
                return []
            rows = await db.execute(select(Job).where(Job.id.in_(ids)))
            claimed = list(rows.scalars().all())
            await db.commit()
            return claimed

    @staticmethod
    async def list_running(user_id: str | None = None) -> list[Job]:
        """Rows left `running` — used to clean up after a restart."""
        async with await get_db() as db:
            stmt = select(Job).where(Job.status == STATUS_RUNNING)
            if user_id:
                stmt = stmt.where(Job.user_id == user_id)
            result = await db.execute(stmt)
            return list(result.scalars().all())


# ── Legacy mirror ────────────────────────────────────────────
# `workspace_todos` stays readable for one release (design note §8.4): the chat
# tools and anything else still selecting from it keep working, and a rollback
# lands on a populated table. Every write through Job keeps it in lockstep.
# This block dies with the table.


async def _mirror_upsert(db, job: Job) -> None:
    from cptr.models.todos import WorkspaceTodo

    existing = await db.get(WorkspaceTodo, job.id)
    if existing:
        existing.title = job.title
        existing.status = _legacy_todo_status(job.status)
        existing.updated_at = job.updated_at
        return
    db.add(
        WorkspaceTodo(
            id=job.id,
            user_id=job.user_id,
            workspace=job.workspace,
            title=job.title,
            status=_legacy_todo_status(job.status),
            source=job.source,
            created_at=job.created_at,
            updated_at=job.updated_at,
        )
    )


async def _mirror_update(db, job_id: str, values: dict) -> None:
    from cptr.models.todos import WorkspaceTodo

    patch: dict = {"updated_at": values.get("updated_at") or int(time.time() * 1000)}
    if "status" in values:
        patch["status"] = _legacy_todo_status(values["status"])
    if "title" in values:
        patch["title"] = values["title"]
    await db.execute(update(WorkspaceTodo).where(WorkspaceTodo.id == job_id).values(**patch))


async def _mirror_delete(db, job_id: str) -> None:
    from cptr.models.todos import WorkspaceTodo

    await db.execute(delete(WorkspaceTodo).where(WorkspaceTodo.id == job_id))


# ── Convenience builders ─────────────────────────────────────


async def create_human_todo(
    user_id: str,
    workspace: str,
    title: str,
    created_at: int,
    source: str = "human",
    origin_chat: str | None = None,
) -> Job:
    """A plain human task: `executor='human'`, `trigger='manual'`, mirrored."""
    return await Job.create(
        user_id=user_id,
        workspace=workspace,
        title=title,
        created_at=created_at,
        source=source,
        origin_chat=origin_chat,
        meta={"legacy": "workspace_todos"},
        mirror_legacy=True,
    )
