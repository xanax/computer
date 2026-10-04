"""A paused task must not advertise fire times.

`next_n_runs_ns` reads the rrule and nothing else, and `trigger_at` survives a
pause, so both used to keep reporting scheduled occurrences for a task that the
scheduler would never run (`promote_due_templates` requires
`status == STATUS_OPEN`). The board then showed "next 5 runs" for a paused row.

The scheduler behaviour itself was always correct - this is about what the API
and the tools *report*.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cptr.models.base import Base
from cptr.utils import db as dbmod

USER = "user-1"
WS = "/tmp/ws"
RRULE = "FREQ=DAILY;BYHOUR=3;BYMINUTE=30;BYSECOND=0"


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def jobs_db(tmp_path, monkeypatch):
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


def _make_job(**over):
    from cptr.models.jobs import KIND_TASK, TRIGGER_RRULE, Job

    now_ms = int(time.time() * 1000)
    vals = {
        "user_id": USER,
        "workspace": WS,
        "title": "Nightly cptr data backup",
        "kind": KIND_TASK,
        "executor": "deepseek-flash",
        "trigger": TRIGGER_RRULE,
        "trigger_at": time.time_ns(),
        "rrule": RRULE,
        "payload": "back things up",
        "created_at": now_ms,
    }
    vals.update(over)
    return _run(Job.create(**vals))


# ---------------------------------------------------------------------------
# The router serializer
# ---------------------------------------------------------------------------


def test_paused_task_reports_no_next_run(jobs_db):
    from cptr.models.jobs import STATUS_PAUSED
    from cptr.routers.automations import _automation_dict

    job = _make_job(status=STATUS_PAUSED)
    d = _automation_dict(job)

    assert d["is_active"] is False
    assert d["next_run_at"] is None, "a paused task must not advertise next_run_at"
    assert d["next_runs"] is None, "a paused task must not advertise next_runs"


def test_open_task_still_reports_its_next_run(jobs_db):
    """The fix must not silence paused-adjacent cases: open tasks still preview."""
    from cptr.models.jobs import STATUS_OPEN
    from cptr.routers.automations import _automation_dict
    from cptr.utils.automations import next_n_runs_ns

    job = _make_job(status=STATUS_OPEN)
    d = _automation_dict(job, next_runs=next_n_runs_ns(job.rrule, 5))

    assert d["is_active"] is True
    assert d["next_run_at"] == job.trigger_at
    assert d["next_runs"], "an open task must still preview its upcoming runs"


# ---------------------------------------------------------------------------
# The list_automations tool
# ---------------------------------------------------------------------------


def test_list_automations_hides_next_runs_when_paused(jobs_db):
    import json

    from cptr.models.jobs import STATUS_OPEN, STATUS_PAUSED
    from cptr.utils import tools

    ctx = {"user_id": USER, "chat_id": None, "workspace": WS}

    _make_job(title="paused one", status=STATUS_PAUSED)
    _make_job(title="open one", status=STATUS_OPEN)

    out = json.loads(_run(tools.list_automations(count=10, __context__=ctx)))
    by_name = {a["name"]: a for a in out["automations"]}

    assert by_name["paused one"]["next_runs"] is None
    assert by_name["open one"]["next_runs"], "open task must keep its preview"

    filtered = json.loads(_run(tools.list_automations(status="paused", count=10, __context__=ctx)))
    assert all(a["next_runs"] is None for a in filtered["automations"])