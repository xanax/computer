"""Tests for the jobs table (Phase 1) and the deferred-run scheduler (Phase 2).

Design: ``notes/NOTES-myriad-job-queue.md``.

The DB fixtures point ``cptr.utils.db`` at a throwaway SQLite file. `NullPool`
keeps every connection inside the event loop that opened it, so the async tests
can each call ``asyncio.run`` independently.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cptr.models.base import Base
from cptr.utils import db as dbmod
from cptr.utils import tools
from cptr.utils.tools import BUILTIN_TOOL_GROUPS, TOOLS

USER = "user-1"
WS = "/tmp/ws"


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def jobs_db(tmp_path, monkeypatch):
    """Point cptr's async session factory at a fresh SQLite file."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}", poolclass=NullPool)
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


@pytest.fixture
def request_obj():
    """A Request with a session cookie, for hitting the routers' functions."""
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/__test__",
            "query_string": b"",
            "headers": [(b"cookie", b"cptr_session=test-token")],
            "client": ("127.0.0.1", 0),
            "server": ("test", 0),
            "scheme": "http",
        }
    )


@pytest.fixture
def auth(monkeypatch):
    """Authenticate every router request as USER."""
    from cptr.utils.config import AuthResult

    result = AuthResult(user_id=USER, username="tester", role="admin")

    for module in ("cptr.routers.todos", "cptr.routers.jobs"):
        monkeypatch.setattr(f"{module}.check_access", lambda **_: result)
    return result


async def _todo(todo_id: str):
    from cptr.models.jobs import Job

    return await Job.get_by_id(todo_id)


# ---------------------------------------------------------------------------
# Model: the row itself
# ---------------------------------------------------------------------------


def test_human_todo_mirrors_workspace_todos(jobs_db):
    from cptr.models.jobs import Job, create_human_todo
    from cptr.models.todos import WorkspaceTodo

    async def main():
        job = await create_human_todo(USER, WS, "water the plants", created_at=1000)
        assert (job.executor, job.trigger, job.status, job.kind) == ("human", "manual", "open", "task")

        rows = await WorkspaceTodo.list_for_workspace(USER, WS)
        assert [(r.id, r.title, r.status, r.source) for r in rows] == [
            (job.id, "water the plants", "open", "human")
        ]
        return job

    job = _run(main())

    # The mirror is a copy under the same id -- links keep working.
    assert job.id

    async def done():
        await Job.update_status(job.id, "done", 2000)

    _run(done())

    async def check():
        from cptr.models.todos import WorkspaceTodo

        mirror = await WorkspaceTodo.get_by_id(job.id)
        assert mirror.status == "done"

    _run(check())


def test_agent_outcomes_do_not_complete_the_todo(jobs_db):
    """needs_review is not done: a model finishing work leaves the todo open."""
    from cptr.models.jobs import Job, create_human_todo
    from cptr.models.todos import WorkspaceTodo

    async def main():
        job = await create_human_todo(USER, WS, "rebase the branch", created_at=1000)
        for status in ("queued", "running", "needs_review", "failed"):
            await Job.update_status(job.id, status, 2000)
            mirror = await WorkspaceTodo.get_by_id(job.id)
            assert mirror.status == "open", status

        await Job.update_status(job.id, "done", 2000)
        assert (await WorkspaceTodo.get_by_id(job.id)).status == "done"

    _run(main())


def test_delete_removes_the_mirror(jobs_db):
    from cptr.models.jobs import Job, create_human_todo
    from cptr.models.todos import WorkspaceTodo

    async def main():
        job = await create_human_todo(USER, WS, "old thing", created_at=1000)
        assert await Job.delete(job.id) is True
        assert await Job.get_by_id(job.id) is None
        assert await WorkspaceTodo.get_by_id(job.id) is None

    _run(main())


def test_a_deferred_job_keeps_its_place(jobs_db):
    from cptr.models.jobs import Job, create_human_todo

    async def main():
        job = await create_human_todo(USER, WS, "run the report", created_at=1000)
        await Job.update_by_id(
            job.id,
            executor="deepseek-flash",
            trigger="at",
            trigger_at=5_000_000_000_000,
            payload="use last quarter's numbers",
        )
        updated = await Job.get_by_id(job.id)
        assert updated.executor == "deepseek-flash"
        assert updated.trigger == "at"
        assert updated.payload == "use last quarter's numbers"
        # Only the schedule changed: it is still an open point on the board.
        assert updated.status == "open"
        assert updated.title == "run the report"

    _run(main())


def test_list_and_counts(jobs_db):
    from cptr.models.jobs import Job, create_human_todo

    async def main():
        a = await create_human_todo(USER, WS, "one", created_at=1000)
        await create_human_todo(USER, WS, "two", created_at=2000)
        await Job.create(USER, "/tmp/other-ws", "elsewhere", created_at=3000)
        await Job.create(USER + "-2", WS, "someone else's", created_at=4000)
        await Job.update_status(a.id, "done", 5000)

        jobs = await Job.list_for_workspace(USER, WS)
        assert [j.title for j in jobs] == ["one", "two"]  # oldest first

        open_only = await Job.list_for_workspace(USER, WS, statuses=("open",))
        assert [j.title for j in open_only] == ["two"]

        assert await Job.count_by_status(USER, WS) == {"done": 1, "open": 1}
        assert [j.title for j in await Job.list_for_workspace(USER, WS, limit=1)] == ["one"]

    _run(main())


# ---------------------------------------------------------------------------
# The clock
# ---------------------------------------------------------------------------


def test_due_jobs_fire_once_and_only_when_due(jobs_db):
    from cptr.models.jobs import Job

    async def main():
        now = 1_000_000_000_000_000_000

        async def defer(title, trigger_at, executor="deepseek-flash"):
            return await Job.create(
                USER,
                WS,
                title,
                executor=executor,
                trigger="at",
                trigger_at=trigger_at,
                created_at=1000,
            )

        due = await defer("due now", now - 1)
        later = await defer("tomorrow", now + 10**12)
        human = await defer("mine", now - 1, executor="human")
        manual = await Job.create(USER, WS, "no trigger time", created_at=1000)

        fired = await Job.mark_due_queued(now)
        assert fired == [due.id]

        # Firing is a one-shot claim: the next poll finds nothing.
        assert await Job.mark_due_queued(now) == []
        assert (await Job.get_by_id(due.id)).status == "queued"
        assert (await Job.get_by_id(later.id)).status == "open"
        assert (await Job.get_by_id(human.id)).status == "open"
        assert (await Job.get_by_id(manual.id)).status == "open"

    _run(main())


def test_claim_bumps_attempts_and_never_double_claims(jobs_db):
    from cptr.models.jobs import Job

    async def main():
        now = 1_000_000_000_000_000_000
        await Job.create(
            USER,
            WS,
            "due",
            executor="deepseek-flash",
            trigger="at",
            trigger_at=now - 1,
            created_at=1000,
        )
        await Job.mark_due_queued(now)

        claimed = await Job.claim_due()
        assert len(claimed) == 1
        assert claimed[0].status == "running"
        assert claimed[0].attempts == 1

        assert await Job.claim_due() == []
        assert len(await Job.list_running()) == 1

    _run(main())


def test_human_rows_are_never_claimed(jobs_db):
    from cptr.models.jobs import Job, create_human_todo

    async def main():
        await create_human_todo(USER, WS, "do the taxes", created_at=1000)
        # A human row that somehow reached 'queued' still needs a person.
        jobs = await Job.list_for_workspace(USER, WS)
        await Job.update_status(jobs[0].id, "queued", 2000)
        assert await Job.claim_due() == []

    _run(main())


def test_recover_marks_interrupted_runs_failed(jobs_db):
    from cptr.models.jobs import Job
    from cptr.utils.jobs import recover_jobs

    async def main():
        job = await Job.create(
            USER,
            WS,
            "interrupted",
            executor="deepseek-flash",
            trigger="at",
            trigger_at=1,
            created_at=1000,
        )
        await Job.update_status(job.id, "running", 2000)
        await recover_jobs()

        recovered = await Job.get_by_id(job.id)
        assert recovered.status == "failed"
        assert recovered.last_error == "interrupted by restart"

    _run(main())


async def _running_run(parent_chat: str | None = None):
    """A `running` job plus the assistant message its run writes into."""
    from cptr.models import Chat, ChatMessage
    from cptr.models.jobs import Job

    chat = await Chat.create(user_id=USER, title="run chat", created_at=2000)
    job = await Job.create(
        USER,
        WS,
        "run me",
        executor="some-model",
        trigger="at",
        trigger_at=1,
        parent_chat=parent_chat,
        created_at=1000,
    )
    await Job.update_status(job.id, "running", 2000)
    message = await ChatMessage.create(
        chat_id=chat.id, role="assistant", content="", done=False, created_at=2000
    )
    return job, chat, message


def test_watcher_settles_a_finished_run_as_needs_review(jobs_db, monkeypatch):
    """A clean run lands in the review queue -- never `done`."""
    from cptr.models import ChatMessage
    from cptr.utils import jobs as jobmod

    monkeypatch.setattr(jobmod, "_WATCH_INTERVAL_S", 0.01)

    async def main():
        job, _chat, message = await _running_run()
        watcher = asyncio.create_task(jobmod._watch_run(job.id, message.id))
        await asyncio.sleep(0.05)
        # Mid-stream: still 'running', not settled early.
        assert (await jobmod.Job.get_by_id(job.id)).status == "running"

        await ChatMessage.update(message.id, done=True, content="hello from the queue")
        await asyncio.wait_for(watcher, 5)
        return await jobmod.Job.get_by_id(job.id)

    finished = _run(main())
    assert finished.status == "needs_review"
    assert finished.last_error is None


def test_watcher_tells_the_dashboard_the_run_settled(jobs_db, monkeypatch):
    """A settled run is a state change an open tab should see without a reload."""
    import cptr.socket.main as socket_main
    from cptr.models import ChatMessage
    from cptr.utils import jobs as jobmod

    monkeypatch.setattr(jobmod, "_WATCH_INTERVAL_S", 0.01)
    told: list[tuple[str, str]] = []

    async def record(user_id, workspace):
        told.append((user_id, workspace))

    monkeypatch.setattr(socket_main, "emit_jobs_changed", record)

    async def main():
        job, _chat, message = await _running_run()
        watcher = asyncio.create_task(jobmod._watch_run(job.id, message.id))
        await asyncio.sleep(0.05)
        await ChatMessage.update(message.id, done=True, content="done")
        await asyncio.wait_for(watcher, 5)

    _run(main())
    assert told == [(USER, WS)]


def test_watcher_fails_a_run_that_errored(jobs_db, monkeypatch):
    from cptr.models import ChatMessage
    from cptr.utils import jobs as jobmod

    monkeypatch.setattr(jobmod, "_WATCH_INTERVAL_S", 0.01)

    async def main():
        job, _chat, message = await _running_run()
        watcher = asyncio.create_task(jobmod._watch_run(job.id, message.id))
        await asyncio.sleep(0.05)
        await ChatMessage.update(message.id, done=True, meta={"error": "stream died"})
        await asyncio.wait_for(watcher, 5)
        return await jobmod.Job.get_by_id(job.id)

    failed = _run(main())
    assert failed.status == "failed"
    assert failed.last_error == "stream died"


def test_watcher_fails_when_the_run_message_vanishes(jobs_db, monkeypatch):
    from cptr.models import ChatMessage
    from cptr.utils import jobs as jobmod

    monkeypatch.setattr(jobmod, "_WATCH_INTERVAL_S", 0.01)

    async def main():
        job, _chat, message = await _running_run()
        watcher = asyncio.create_task(jobmod._watch_run(job.id, message.id))
        await asyncio.sleep(0.05)
        await ChatMessage.delete(message.id)
        await asyncio.wait_for(watcher, 5)
        return await jobmod.Job.get_by_id(job.id)

    failed = _run(main())
    assert failed.status == "failed"
    assert failed.last_error == "run message disappeared"


def test_watcher_leaves_a_cancelled_job_alone(jobs_db, monkeypatch):
    """The watcher reports; whoever cancels the job owns the outcome."""
    from cptr.models import ChatMessage
    from cptr.utils import jobs as jobmod

    monkeypatch.setattr(jobmod, "_WATCH_INTERVAL_S", 0.01)

    async def main():
        job, _chat, message = await _running_run()
        watcher = asyncio.create_task(jobmod._watch_run(job.id, message.id))
        await asyncio.sleep(0.05)
        await jobmod.Job.update_status(job.id, "cancelled", 3000, last_error="cancelled by user")
        await ChatMessage.update(message.id, done=True, content="too late")
        await asyncio.wait_for(watcher, 5)
        return await jobmod.Job.get_by_id(job.id)

    still_cancelled = _run(main())
    assert still_cancelled.status == "cancelled"
    assert still_cancelled.last_error == "cancelled by user"


def test_a_busy_chat_requeues_the_job(jobs_db, monkeypatch):
    """A job whose chat is mid-turn waits its turn instead of failing."""
    from types import SimpleNamespace

    from cptr.models import Chat, ChatMessage
    from cptr.utils import identity, jobs as jobmod, model_targets

    monkeypatch.setattr(jobmod, "_WATCH_INTERVAL_S", 0.01)

    async def fake_target(model_id, app_state=None):
        return model_targets.ApiModelTarget(
            kind="api", connection={}, runtime_model="some-model", full_model_id="some-model"
        )

    async def fake_request(app, user_id):
        return SimpleNamespace(app=app)

    monkeypatch.setattr(model_targets, "resolve_model_target", fake_target)
    monkeypatch.setattr(identity, "internal_request_for_user", fake_request)
    app = SimpleNamespace(state=SimpleNamespace())

    async def main():
        parent = await Chat.create(user_id=USER, title="busy", created_at=1000)
        await ChatMessage.create(
            chat_id=parent.id, role="assistant", content="", done=False, created_at=1000
        )
        job, _chat, _message = await _running_run(parent_chat=parent.id)

        await jobmod.run_job(app, job)
        return await jobmod.Job.get_by_id(job.id)

    requeued = _run(main())
    assert requeued.status == "queued"
    assert requeued.last_error is None
    # It keeps its place in line: a retry is not a new attempt in the books yet.
    assert requeued.attempts == 0


def test_a_cancel_before_the_turn_starts_never_runs(jobs_db, monkeypatch):
    """Stop means stop: a cancel landing during preparation must not run the job.

    Observed live 2026-09-20: `_prepare_run` wrote `running`, the human pressed
    Stop 25 ms later, and `/cancel` found no task registered yet -- so it only
    flipped the row, and the model went on to work for two more minutes.
    """
    from types import SimpleNamespace

    from cptr.models import Chat, ChatMessage
    from cptr.utils import chat_task, identity, jobs as jobmod, model_targets

    started: list[str] = []

    def fake_start_task(request, *, message_id, **kwargs):
        started.append(message_id)

    async def fake_target(model_id, app_state=None):
        return model_targets.ApiModelTarget(
            kind="api", connection={}, runtime_model="some-model", full_model_id="some-model"
        )

    async def fake_request(app, user_id):
        return SimpleNamespace(app=app)

    async def fake_emit(user_id, payload):
        return None

    monkeypatch.setattr(model_targets, "resolve_model_target", fake_target)
    monkeypatch.setattr(identity, "internal_request_for_user", fake_request)
    monkeypatch.setattr(chat_task, "start_task", fake_start_task)
    monkeypatch.setattr(jobmod, "emit_to_user", fake_emit, raising=False)

    app = SimpleNamespace(state=SimpleNamespace())

    async def main():
        chat = await Chat.create(user_id=USER, title="run chat", created_at=1000)
        prompt = await ChatMessage.create(
            chat_id=chat.id, role="user", content="do it", created_at=1000
        )
        message = await ChatMessage.create(
            chat_id=chat.id,
            role="assistant",
            content="",
            parent_id=prompt.id,
            done=False,
            created_at=1000,
        )
        job = await jobmod.Job.create(
            USER, WS, "cancelled mid-prep", executor="some-model", trigger="at", trigger_at=1
        )
        await jobmod.Job.update_status(job.id, "running", 2000)
        # The human presses Stop while run_job is still getting ready: the row is
        # cancelled before start_task has registered anything to cancel.
        await jobmod.Job.update_status(job.id, "cancelled", 2001, trigger="manual")

        async def fake_prepare(app_, job_):
            return chat, message, await fake_target(job_.executor), ""

        monkeypatch.setattr(jobmod, "_prepare_run", fake_prepare)
        await jobmod.run_job(app, await jobmod.Job.get_by_id(job.id))

        return (
            await jobmod.Job.get_by_id(job.id),
            await ChatMessage.get_by_id(message.id),
        )

    job, message = _run(main())
    assert started == [], "the turn must not start once the row is cancelled"
    assert job.status == "cancelled"
    # The chat must not be left hanging as a pending turn.
    assert message.done is True
    assert message.content == "cancelled"


def test_prompt_tells_the_agent_it_is_alone(jobs_db):
    from cptr.models.jobs import Job
    from cptr.utils.jobs import build_prompt

    async def main():
        job = await Job.create(
            USER,
            WS,
            "nightly review",
            executor="deepseek-flash",
            trigger="at",
            trigger_at=1_700_000_000_000_000_000,
            created_at=1000,
        )
        job.payload = "summarise the week"
        prompt = build_prompt(job)
        assert "summarise the week" in prompt  # payload wins over title
        assert "2023-11-14T22:13:20Z" in prompt
        assert "complete_workspace_todo" in prompt
        return job

    job = _run(main())

    async def check():
        from cptr.models.jobs import Job as J
        from cptr.utils.jobs import build_prompt as bp

        bare = await J.create(USER, WS, "no payload", created_at=1000)
        assert "Task: no payload" in bp(bare)

    _run(check())


# ---------------------------------------------------------------------------
# Router: /api/todos is a shim over jobs
# ---------------------------------------------------------------------------


def test_todos_shim_round_trip(jobs_db, auth, request_obj):
    from cptr.routers.todos import AddTodoRequest, add_todo, list_todos, remove_todo, toggle_todo

    async def main():
        created = await add_todo(request_obj, AddTodoRequest(workspace=WS, title="  ship it  "))
        # Same response shape as before the refactor.
        assert set(created) == {
            "id",
            "workspace",
            "title",
            "status",
            "source",
            "created_at",
            "updated_at",
        }
        assert created["title"] == "ship it"
        assert created["status"] == "open"
        assert created["source"] == "human"

        listed = await list_todos(request_obj, workspace=WS)
        assert set(listed) == {"todos", "pending_requests"}
        assert [t["id"] for t in listed["todos"]] == [created["id"]]

        done = await toggle_todo(request_obj, created["id"])
        assert done["status"] == "done"
        assert (await toggle_todo(request_obj, created["id"]))["status"] == "open"

        assert await remove_todo(request_obj, created["id"]) == {"ok": True}
        assert (await list_todos(request_obj, workspace=WS))["todos"] == []

    _run(main())


def test_approving_a_chat_addition_creates_a_job(jobs_db, auth, request_obj):
    from cptr.models.todos import TodoRequest
    from cptr.routers.todos import approve_request, list_todos
    from cptr.utils.config import now_ms

    async def main():
        req = await TodoRequest.create(
            user_id=USER, workspace=WS, action="add", created_at=now_ms(), title="from chat"
        )
        assert (await list_todos(request_obj, workspace=WS))["pending_requests"] == [
            {
                "id": req.id,
                "workspace": WS,
                "action": "add",
                "todo_id": None,
                "title": "from chat",
                "status": "pending",
                "created_at": req.created_at,
                "resolved_at": None,
            }
        ]

        assert await approve_request(request_obj, req.id) == {"ok": True}
        todos = (await list_todos(request_obj, workspace=WS))["todos"]
        assert [(t["title"], t["source"]) for t in todos] == [("from chat", "chat")]

    _run(main())


def test_finishing_a_deferred_todo_cancels_the_run(jobs_db, auth, request_obj):
    from cptr.models.jobs import Job
    from cptr.routers.todos import AddTodoRequest, add_todo, toggle_todo

    async def main():
        created = await add_todo(
            request_obj, AddTodoRequest(workspace=WS, title="deferred work")
        )
        await Job.update_by_id(
            created["id"], executor="deepseek-flash", trigger="at", trigger_at=9 * 10**18
        )

        toggled = await toggle_todo(request_obj, created["id"])
        assert toggled["status"] == "done"

        job = await Job.get_by_id(created["id"])
        assert (job.executor, job.trigger, job.trigger_at) == ("human", "manual", None)
        assert "defer_cancelled_at" in (job.meta or {})

    _run(main())


def test_deferred_todo_still_lists_as_open(jobs_db, auth, request_obj):
    """A queued or awaiting-review deferral is outstanding, not finished."""
    from cptr.models.jobs import Job
    from cptr.routers.todos import AddTodoRequest, add_todo, list_todos

    async def main():
        created = await add_todo(request_obj, AddTodoRequest(workspace=WS, title="deferred"))
        for status in ("queued", "running", "needs_review"):
            await Job.update_status(created["id"], status, 2000)
            listed = await list_todos(request_obj, workspace=WS)
            assert listed["todos"][0]["status"] == "open", status

    _run(main())


def test_non_todo_jobs_stay_off_the_todo_list(jobs_db, auth, request_obj):
    from cptr.models.jobs import Job
    from cptr.routers.todos import list_todos

    async def main():
        await Job.create(USER, WS, "a note", kind="note", created_at=1000)
        await Job.create(
            USER, WS, "recurring", trigger="rrule", rrule="FREQ=DAILY", created_at=2000
        )
        assert (await list_todos(request_obj, workspace=WS))["todos"] == []

    _run(main())


# ---------------------------------------------------------------------------
# Router: /api/jobs
# ---------------------------------------------------------------------------


def test_jobs_api_create_patch_and_delete(jobs_db, auth, request_obj):
    from cptr.routers.jobs import (
        CreateJobRequest,
        PatchJobRequest,
        create_job,
        delete_job,
        get_job,
        list_jobs,
        patch_job,
    )
    from cptr.routers.todos import list_todos

    async def main():
        job = await create_job(
            request_obj, CreateJobRequest(workspace=WS, title="morning report")
        )
        assert job["executor"] == "human" and job["trigger"] == "manual"

        listed = await list_jobs(request_obj, workspace=WS)
        assert [j["id"] for j in listed["jobs"]] == [job["id"]]
        assert listed["counts"] == {"open": 1}
        assert "open" in listed["open_statuses"]

        # A human job created here shows up as a todo too (mirror).
        assert [t["id"] for t in (await list_todos(request_obj, workspace=WS))["todos"]] == [
            job["id"]
        ]

        patched = await patch_job(
            request_obj, job["id"], PatchJobRequest(title="evening report", priority=3)
        )
        assert patched["title"] == "evening report"
        assert (await get_job(request_obj, job["id"]))["priority"] == 3

        assert await delete_job(request_obj, job["id"]) == {"ok": True}
        assert (await list_jobs(request_obj, workspace=WS))["jobs"] == []
        # ...and stops being a todo.
        assert (await list_todos(request_obj, workspace=WS))["todos"] == []

    _run(main())


def test_jobs_api_validates_schedules(jobs_db, auth, request_obj):
    from datetime import datetime, timedelta, timezone

    from fastapi import HTTPException

    from cptr.routers.jobs import CreateJobRequest, create_job

    soon = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    async def main():
        created = await create_job(
            request_obj,
            CreateJobRequest(
                workspace=WS,
                title="later",
                executor="deepseek-flash",
                trigger="at",
                at=soon,
            ),
        )
        assert created["trigger"] == "at"
        assert created["trigger_at"] > 0

        for body in (
            CreateJobRequest(workspace=WS, title="", at=None),
            CreateJobRequest(workspace=WS, title="x", trigger="at"),  # no at
            CreateJobRequest(workspace=WS, title="x", trigger="at", at="yesterday"),  # past
            CreateJobRequest(workspace=WS, title="x", trigger="rrule"),  # no rrule
            CreateJobRequest(workspace=WS, title="x", kind="weird"),
        ):
            with pytest.raises(HTTPException) as excinfo:
                await create_job(request_obj, body)
            assert excinfo.value.status_code == 400

    _run(main())


def test_jobs_api_defer_and_cancel(jobs_db, auth, request_obj):
    from cptr.routers.jobs import (
        CreateJobRequest,
        DeferJobRequest,
        cancel_job,
        create_job,
        defer_job,
        get_job,
    )
    from cptr.routers.todos import AddTodoRequest, add_todo

    async def main():
        todo = await add_todo(request_obj, AddTodoRequest(workspace=WS, title="night run"))

        deferred = await defer_job(
            request_obj,
            todo["id"],
            DeferJobRequest(at="in 2 hours", executor="deepseek-flash", payload="be thorough"),
        )
        assert deferred["executor"] == "deepseek-flash"
        assert deferred["trigger"] == "at"
        assert deferred["status"] == "open"  # still the human's row until it fires
        assert deferred["payload"] == "be thorough"
        assert deferred["meta"]["deferred_by"] == "api"

        cancelled = await cancel_job(request_obj, todo["id"])
        assert cancelled["status"] == "cancelled"
        assert cancelled["last_error"] == "cancelled by user"

        job = await create_job(request_obj, CreateJobRequest(workspace=WS, title="plain"))
        assert (await get_job(request_obj, job["id"]))["status"] == "open"
        assert (await cancel_job(request_obj, job["id"]))["status"] == "cancelled"

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as excinfo:
            await cancel_job(request_obj, job["id"])
        assert excinfo.value.status_code == 409

    _run(main())


# ---------------------------------------------------------------------------
# Tool: defer_workspace_todo
# ---------------------------------------------------------------------------


def test_defer_tool_is_registered():
    assert "defer_workspace_todo" in BUILTIN_TOOL_GROUPS["todos"]
    assert TOOLS["defer_workspace_todo"]["approval"] == "review"


def test_defer_tool_schedules_the_job(jobs_db):
    import json

    from cptr.models.jobs import Job, create_human_todo

    ctx = {
        "workspace": WS,
        "user_id": USER,
        "chat_id": "chat-1",
        "model_id": "deepseek-flash",
        "full_model_id": "deepseek-flash",
    }

    async def main():
        job = await create_human_todo(USER, WS, "write the changelog", created_at=1000)
        payload = json.loads(
            await tools.defer_workspace_todo(
                todo_id=job.id, at="30m", instructions="cover Phase 1 and 2", __context__=ctx
            )
        )
        assert payload["status"] == "deferred"
        assert payload["executor"] == "deepseek-flash"
        assert payload["runs_in"] == "its own chat"
        assert payload["run_at"].endswith("Z")

        scheduled = await Job.get_by_id(job.id)
        assert scheduled.trigger == "at"
        assert scheduled.executor == "deepseek-flash"
        assert scheduled.payload == "cover Phase 1 and 2"
        assert scheduled.parent_chat is None
        assert scheduled.meta["deferred_by"] == "chat"

        # Resume-here keeps timer semantics: wake this conversation.
        second = await tools.defer_workspace_todo(
            todo_id=job.id, at="1h", resume_here=True, __context__=ctx
        )
        assert json.loads(second)["runs_in"] == "this chat"
        assert (await Job.get_by_id(job.id)).parent_chat == "chat-1"

    _run(main())


def test_defer_tool_rejects_bad_input(jobs_db):
    import json

    from cptr.models.jobs import Job, create_human_todo

    ctx = {"workspace": WS, "user_id": USER, "chat_id": "chat-1", "model_id": "m"}

    async def main():
        job = await create_human_todo(USER, WS, "a task", created_at=1000)

        missing = json.loads(
            await tools.defer_workspace_todo(todo_id="nope", at="10m", __context__=ctx)
        )
        assert "not found" in missing["error"]

        bad_time = json.loads(
            await tools.defer_workspace_todo(todo_id=job.id, at="never", __context__=ctx)
        )
        assert "relative time" in bad_time["error"]

        other_ws = json.loads(
            await tools.defer_workspace_todo(
                todo_id=job.id, at="10m", __context__={**ctx, "workspace": "/elsewhere"}
            )
        )
        assert "not found" in other_ws["error"]

        assert (await Job.get_by_id(job.id)).trigger == "manual"

    _run(main())


def test_defer_tool_refuses_a_finished_todo(jobs_db):
    import json

    from cptr.models.jobs import Job, create_human_todo

    ctx = {"workspace": WS, "user_id": USER, "chat_id": "chat-1", "model_id": "m"}

    async def main():
        job = await create_human_todo(USER, WS, "already done", created_at=1000)
        await Job.update_status(job.id, "done", 2000)
        out = json.loads(
            await tools.defer_workspace_todo(todo_id=job.id, at="10m", __context__=ctx)
        )
        assert out["error"] == "Todo is already done"

    _run(main())


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def _migrated_db(tmp_path: Path) -> Path:
    """Run the alembic chain up to 0008 (pre-jobs) on a scratch DB."""
    from alembic import command
    from alembic.config import Config

    db_file = tmp_path / "migration.db"
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(dbmod.__file__).parent.parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_file}")
    command.upgrade(cfg, "0008")
    return db_file


def test_migration_backfills_every_todo(tmp_path, monkeypatch):
    """0009 copies workspace_todos into jobs under the same ids -- no drops."""
    from alembic import command
    from alembic.config import Config

    db_file = _migrated_db(tmp_path)

    conn = sqlite3.connect(db_file)
    conn.executemany(
        "INSERT INTO workspace_todos (id, user_id, workspace, title, status, source,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        [
            ("t-1", USER, WS, "open one", "open", "human", 1000, 1000),
            ("t-2", USER, WS, "done one", "done", "chat", 2000, 3000),
        ],
    )
    conn.commit()
    conn.close()

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(dbmod.__file__).parent.parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_file}")
    command.upgrade(cfg, "0009")

    conn = sqlite3.connect(db_file)
    rows = conn.execute(
        "SELECT id, title, status, executor, trigger, source, created_at, updated_at"
        " FROM jobs ORDER BY created_at"
    ).fetchall()
    todos_after = conn.execute("SELECT COUNT(*) FROM workspace_todos").fetchone()[0]
    conn.close()

    assert rows == [
        ("t-1", "open one", "open", "human", "manual", "human", 1000, 1000),
        ("t-2", "done one", "done", "human", "manual", "chat", 2000, 3000),
    ]
    assert todos_after == 2  # nothing dropped


def test_migration_indexes_exist(tmp_path):
    db_file = _migrated_db(tmp_path)

    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(dbmod.__file__).parent.parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_file}")
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_file)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    conn.close()
    assert {"ix_jobs_user_ws_status", "ix_jobs_due", "ix_jobs_resource"} <= names


def test_migration_downgrade_leaves_todos_alone(tmp_path):
    db_file = _migrated_db(tmp_path)
    conn = sqlite3.connect(db_file)
    conn.execute(
        "INSERT INTO workspace_todos (id, user_id, workspace, title, status, source,"
        " created_at, updated_at) VALUES ('t-9',?,?,'x','open','human',1,1)",
        (USER, WS),
    )
    conn.commit()
    conn.close()

    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(dbmod.__file__).parent.parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_file}")
    command.upgrade(cfg, "0009")
    command.downgrade(cfg, "0008")

    conn = sqlite3.connect(db_file)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    todos = conn.execute("SELECT COUNT(*) FROM workspace_todos").fetchone()[0]
    conn.close()

    assert "jobs" not in tables
    assert todos == 1
