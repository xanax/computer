"""Tests for the sidebar workspace ranking.

The score is calibrated against real telemetry, so what these pin is not the
arithmetic but the two things that make the ranking safe to act on: that the
signals combine as documented, and that a workspace with unfinished business is
held visible whatever its score.

Design: notes/NOTES-activity-aware-sidebar.md
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cptr.models.base import Base
from cptr.models.chats import Chat
from cptr.models.jobs import KIND_RUN, STATUS_DONE, STATUS_OPEN, Job
from cptr.models.ui_events import UiEvent
from cptr.utils import db as dbmod
from cptr.utils.workspace_priority import (
    RECENCY_HALF_LIFE_MS,
    WINDOW_DAYS,
    WorkspaceActivity,
    _decay,
    _saturate,
    rank_workspaces,
    score_workspace,
    workspace_priority,
)

USER = "user-1"
DAY_MS = 24 * 60 * 60 * 1000


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def workspace_db(tmp_path, monkeypatch):
    """Point cptr's async session factory at a fresh SQLite file."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'workspace-priority.db'}", poolclass=NullPool
    )
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


async def _add(row) -> None:
    async with await dbmod.get_db() as db:
        db.add(row)
        await db.commit()


def _dwell_rows(path: str, *, seconds: float, days: int, at_ms: float) -> list:
    """One dwell sample per day, so day counts and totals are controllable."""
    return [
        {
            "user_id": USER,
            "workspace": path,
            "kind": "dwell",
            "label": "switch",
            "ts": at_ms - i * DAY_MS,
            "duration_ms": (seconds / days) * 1000.0,
        }
        for i in range(days)
    ]


# ── The curves ──────────────────────────────────────────────────


def test_saturate_reaches_half_at_its_half_point_and_stays_bounded():
    """The squashing curve is what keeps five signals on one scale."""
    assert _saturate(0, 10) == 0.0
    assert _saturate(-5, 10) == 0.0
    assert _saturate(10, 10) == pytest.approx(0.5)
    # Approaches 1 and never reaches it, so the ordering is always total.
    assert _saturate(1_000_000, 10) < 1.0
    assert _saturate(100, 10) > _saturate(50, 10) > _saturate(10, 10)


def test_recency_decay_is_half_at_one_half_life():
    assert _decay(0, RECENCY_HALF_LIFE_MS) == 1.0
    assert _decay(RECENCY_HALF_LIFE_MS, RECENCY_HALF_LIFE_MS) == pytest.approx(0.5)
    assert _decay(2 * RECENCY_HALF_LIFE_MS, RECENCY_HALF_LIFE_MS) == pytest.approx(0.25)


# ── The score ───────────────────────────────────────────────────


def test_score_falls_as_a_workspace_goes_quiet():
    now = 1_800_000_000_000.0
    fresh = WorkspaceActivity(path="/w", dwell_seconds=3600, last_seen_ms=now)
    stale = WorkspaceActivity(path="/w", dwell_seconds=3600, last_seen_ms=now - 40 * DAY_MS)
    assert score_workspace(fresh, now) > score_workspace(stale, now)


def test_frequency_outranks_a_single_long_session():
    """Why `days` is its own signal: a month of daily use beats one weekend
    marathon of the same measured time."""
    now = 1_800_000_000_000.0
    daily = WorkspaceActivity(path="/a", dwell_seconds=7200, active_days=20, last_seen_ms=now)
    once = WorkspaceActivity(path="/b", dwell_seconds=7200, active_days=1, last_seen_ms=now)
    assert score_workspace(daily, now) > score_workspace(once, now)


def test_a_workspace_with_no_history_scores_zero_rather_than_vanishing():
    """It still belongs in the sidebar, just at the bottom."""
    assert score_workspace(WorkspaceActivity(path="/w"), 1_800_000_000_000.0) == 0.0


def test_the_score_is_bounded_to_a_percentage():
    """The weights sum to 1 and every signal is squashed below 1, so the score
    is a percentage by construction. It reaches exactly 100 when everything is
    maxed — the true value is 99.998 and `round(…, 2)` is what lands on the
    ceiling."""
    maxed = WorkspaceActivity(
        path="/w",
        dwell_seconds=10**9,
        active_days=10**6,
        chats=10**6,
        live_jobs=10**6,
        last_seen_ms=1_800_000_000_000.0,
    )
    assert score_workspace(maxed, 1_800_000_000_000.0) == 100.0
    # One notch down from maxed is strictly lower, so the ceiling is not a clamp
    # that flattens real differences.
    maxed.chats = 1
    assert score_workspace(maxed, 1_800_000_000_000.0) < 100.0


# ── Aggregation ─────────────────────────────────────────────────


def test_rank_sorts_by_usage_and_keeps_every_path(workspace_db):
    """Ranking is a partition of the list — nothing is dropped."""

    async def main():
        import time

        now = time.time() * 1000
        await UiEvent.bulk_create(
            _dwell_rows("/w/busy", seconds=8 * 3600, days=6, at_ms=now), int(now)
        )
        await UiEvent.bulk_create(
            _dwell_rows("/w/middle", seconds=2 * 3600, days=3, at_ms=now), int(now)
        )
        return await rank_workspaces(["/w/quiet", "/w/busy", "/w/middle"])

    ranked = _run(main())
    assert [item.path for item in ranked] == ["/w/busy", "/w/middle", "/w/quiet"]
    assert ranked[-1].score == 0.0


def test_usage_older_than_the_window_is_ignored(workspace_db):
    """A project finished two months ago must not keep a row at the top."""

    async def main():
        import time

        now = time.time() * 1000
        old = now - (WINDOW_DAYS + 5) * DAY_MS
        await UiEvent.bulk_create(
            _dwell_rows("/w/old", seconds=40 * 3600, days=20, at_ms=old), int(now)
        )
        return (await rank_workspaces(["/w/old"]))[0]

    item = _run(main())
    assert item.dwell_seconds == 0.0
    assert item.active_days == 0
    assert item.score == 0.0


def test_legacy_timestamps_are_skipped_rather_than_misplaced(workspace_db):
    """Rows written before migration 0006 hold page-relative `ts` values."""

    async def main():
        import time

        await _add(
            UiEvent(
                user_id=USER,
                workspace="/w/legacy",
                kind="dwell",
                label="switch",
                ts=1234.5,  # performance.now(), not epoch ms
                duration_ms=99_000.0,
                created_at=int(time.time() * 1000),
            )
        )
        return await rank_workspaces(["/w/legacy"])

    assert _run(main())[0].dwell_seconds == 0.0


def test_active_days_come_from_dwell_rows_only(workspace_db):
    """A backgrounded tab logging mounts must not invent days of usage."""

    async def main():
        import time

        now = time.time() * 1000
        for i in range(5):
            await _add(
                UiEvent(
                    user_id=USER,
                    workspace="/w/mounty",
                    kind="mount",
                    label="terminal",
                    ts=now - i * DAY_MS,
                    duration_ms=12.0,
                    created_at=int(now),
                )
            )
        return await rank_workspaces(["/w/mounty"])

    item = _run(main())[0]
    assert item.active_days == 0
    assert item.score == 0.0


def test_internal_chats_do_not_count_as_working_here(workspace_db):
    """Subagent traffic is machinery, and it lands in whichever workspace the
    agent happened to be running in."""

    async def main():
        import time

        now = int(time.time() * 1000)
        for _ in range(6):
            await _add(
                Chat(
                    user_id=USER,
                    title="sub",
                    meta={"workspace": "/w/x", "subagent": True},
                    created_at=now,
                    updated_at=now,
                )
            )
        for _ in range(2):
            await _add(
                Chat(
                    user_id=USER,
                    title="real",
                    meta={"workspace": "/w/x"},
                    created_at=now,
                    updated_at=now,
                )
            )
        return await rank_workspaces(["/w/x"])

    assert _run(main())[0].chats == 2


def test_settled_runs_are_not_outstanding_work(workspace_db):
    """A finished run is history; only live tasks hold a workspace open."""

    async def main():
        import time

        now = int(time.time() * 1000)

        def job(workspace, status, kind=KIND_RUN):
            return Job(
                user_id=USER,
                workspace=workspace,
                resource="",
                title="t",
                kind=kind,
                executor="human",
                trigger="manual",
                payload="",
                status=status,
                created_at=now,
                updated_at=now,
            )

        await _add(job("/w/done", STATUS_DONE, KIND_RUN))
        await _add(job("/w/open", STATUS_OPEN, "task"))
        return {item.path: item for item in await rank_workspaces(["/w/done", "/w/open"])}

    ranked = _run(main())
    assert ranked["/w/done"].live_jobs == 0
    assert ranked["/w/open"].live_jobs == 1


def test_equal_scores_do_not_reshuffle_on_every_refetch(workspace_db):
    """An unstable sort would make rows swap places under the pointer."""

    async def main():
        import time

        now = time.time() * 1000
        for path in ("/w/a", "/w/b"):
            await UiEvent.bulk_create(
                _dwell_rows(path, seconds=1000, days=2, at_ms=now), int(now)
            )
        first = [i.path for i in await rank_workspaces(["/w/b", "/w/a"])]
        second = [i.path for i in await rank_workspaces(["/w/a", "/w/b"])]
        return first, second

    first, second = _run(main())
    assert first == second == ["/w/a", "/w/b"]


# ── What gets held visible ───────────────────────────────────────


def test_an_unread_chat_holds_a_workspace_visible(workspace_db):
    async def main():
        import time

        now = int(time.time() * 1000)
        await _add(
            Chat(
                user_id=USER,
                title="unread",
                meta={"workspace": "/w/unread"},
                created_at=now,
                updated_at=now,
                last_read_at=0,
            )
        )
        return await workspace_priority(["/w/unread"])

    result = _run(main())
    assert "/w/unread" in result["pinned"]
    assert result["workspaces"][0]["unread"] == 1
    assert "unread" in result["workspaces"][0]["reasons"]


def test_an_outstanding_task_holds_a_workspace_visible(workspace_db):
    async def main():
        import time

        now = int(time.time() * 1000)
        await _add(
            Job(
                user_id=USER,
                workspace="/w/task",
                resource="",
                title="t",
                kind="task",
                executor="human",
                trigger="manual",
                payload="",
                status=STATUS_OPEN,
                created_at=now,
                updated_at=now,
            )
        )
        return await workspace_priority(["/w/task"])

    result = _run(main())
    assert "/w/task" in result["pinned"]
    assert "tasks" in result["workspaces"][0]["reasons"]


def test_the_open_workspace_is_held_visible(workspace_db):
    """Whatever the score says, the row you are standing in does not move."""

    async def main():
        return await workspace_priority(["/w/active", "/w/other"], current="/w/active")

    result = _run(main())
    assert "/w/active" in result["pinned"]
    assert "current" in result["workspaces"][0]["reasons"]


def test_a_quiet_workspace_with_nothing_pending_is_held_by_nothing(workspace_db):
    assert _run(workspace_priority(["/w/cold"]))["pinned"] == []


def test_the_limit_is_the_caller_suggestion_not_a_cull(workspace_db):
    """`limit` seeds the initial view; the frontend then remembers the user's
    own choice, so no workspace is ever withheld here."""
    result = _run(workspace_priority(["/w/a", "/w/b", "/w/c"], limit=1))
    assert result["limit"] == 1
    assert len(result["workspaces"]) == 3


def test_duplicate_paths_are_ranked_once(workspace_db):
    """The sidebar dedupes by path before rendering; the ranking must agree."""
    ranked = _run(rank_workspaces(["/w/a", "/w/a", "/w/a"]))
    assert len(ranked) == 1