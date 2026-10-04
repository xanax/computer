"""Activity ranking for the sidebar's workspace list.

The sidebar lists every workspace the user has opened, which stops being a
useful list once there are dozens. Rather than cap it at an arbitrary N and
hide whatever happens to sort last, this ranks the user's *own* workspaces by
how much they actually use them, so the ones that matter stay put and the long
tail collapses behind one "Show more" row.

Five signals, all of which the system already records:

  dwell     measured active time per workspace (``UiEvent.dwell_by_workspace``)
  days      distinct days with any tracked time — frequency rather than
            duration, so one marathon session cannot outrank a project touched
            every day for a month
  recency   exponential decay on the last time the workspace was touched
  chats     conversations started or continued in the workspace
  jobs      live tasks that will run in the workspace on their own

Every signal is squashed into 0..1 by a saturating curve before weighting, so
none dominates by unit scale: dwell runs to five figures in seconds, distinct
days to single digits. The weights favour dwell and recency — the closest
proxies for "which projects am I working on" — with chats and tasks as smaller
corrections.

The score is deliberately *stable*, not adaptive. Nothing here changes because a
workspace was merely looked at, which is what lets the frontend pin whatever
the user has chosen to show without it jumping underneath them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select

from cptr.models.chats import Chat
from cptr.models.jobs import (
    KIND_RUN,
    STATUS_BLOCKED,
    STATUS_NEEDS_REVIEW,
    STATUS_OPEN,
    STATUS_PAUSED,
    STATUS_QUEUED,
    STATUS_RUNNING,
    Job,
)
from cptr.models.ui_events import UiEvent
from cptr.utils.config import now_ms
from cptr.utils.db import get_db

# How far back to measure. Three weeks: long enough that a week away from the
# computer does not erase a project, short enough that a finished workspace
# still fades out.
WINDOW_DAYS = 21
WINDOW_MS = WINDOW_DAYS * 24 * 60 * 60 * 1000

# A workspace not touched for a while decays towards 0 with this half-life.
RECENCY_HALF_LIFE_MS = 10 * 24 * 60 * 60 * 1000

# The value at which each signal reads as "half" — the point past which more of
# it stops buying much rank. Calibrated against a real 40-workspace install.
DWELL_HALF = 6 * 60 * 60.0  # 6 hours of measured time inside the window
DAYS_HALF = 8.0  # 8 distinct active days out of the 21
CHATS_HALF = 10.0
JOBS_HALF = 2.0

# Weights, summing to 1 so the score reads as a percentage.
WEIGHT_DWELL = 0.34
WEIGHT_DAYS = 0.20
WEIGHT_RECENCY = 0.22
WEIGHT_CHATS = 0.14
WEIGHT_JOBS = 0.10

# Statuses meaning "this workspace still has work in it". A task waiting on the
# user counts; one already settled does not.
LIVE_JOB_STATUSES = (
    STATUS_OPEN,
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_BLOCKED,
    STATUS_NEEDS_REVIEW,
    STATUS_PAUSED,
)


@dataclass
class WorkspaceActivity:
    """One workspace's usage signals, scored on demand."""

    path: str
    dwell_seconds: float = 0.0
    active_days: int = 0
    chats: int = 0
    live_jobs: int = 0
    unread: int = 0
    last_seen_ms: int = 0
    score: float = 0.0
    # Why this workspace is held visible regardless of rank.
    reasons: list[str] = field(default_factory=list)


def _saturate(value: float, half: float) -> float:
    """Map ``value`` into 0..1, reaching 0.5 at ``half`` and approaching 1.

    Rational rather than a hard clamp: ten times ``half`` still outranks five
    times, but by less and less, so one very long session cannot push the rest
    of the list off the end.
    """
    if value <= 0 or half <= 0:
        return 0.0
    return value / (value + half)


def _decay(age_ms: float, half_life_ms: float) -> float:
    """Exponential decay: 1.0 at age 0, 0.5 after one half-life."""
    if age_ms <= 0:
        return 1.0
    if half_life_ms <= 0:
        return 0.0
    return 0.5 ** (age_ms / half_life_ms)


async def _dwell_by_workspace(since_ms: float) -> dict[str, dict]:
    """Measured dwell seconds, active day count and last touch, per workspace.

    The day count is taken from ``dwell`` rows only. Counting days across all
    event kinds would let a backgrounded tab that logs a mount every 30 seconds
    claim a workspace was used on days it was not.
    """
    day = func.strftime("%Y-%m-%d", UiEvent.ts / 1000.0, "unixepoch")
    async with await get_db() as db:
        result = await db.execute(
            select(
                UiEvent.workspace,
                func.sum(UiEvent.duration_ms),
                func.count(func.distinct(day)),
                func.max(UiEvent.ts),
            )
            .where(
                UiEvent.kind == "dwell",
                # Legacy rows pre-migration 0006 hold page-relative ts values,
                # which cannot be placed on a timeline at all.
                UiEvent.ts > 1e12,
                UiEvent.ts >= since_ms,
            )
            .group_by(UiEvent.workspace)
        )
        rows = result.all()

    out: dict[str, dict] = {}
    for workspace, duration_ms, days, last_ts in rows:
        if not workspace:
            continue
        out[workspace] = {
            "seconds": float(duration_ms or 0.0) / 1000.0,
            "days": int(days or 0),
            "last_seen": int(last_ts or 0),
        }
    return out


async def _chat_activity(since_ms: float) -> dict[str, dict]:
    """Conversation count and last touch per workspace.

    Internal and subagent chats are excluded for the same reason
    ``Chat.unread_counts_by_workspace`` excludes them: they are machinery, and
    they inflate every workspace an agent happens to work in.
    """
    workspace_col = Chat.meta["workspace"].as_string()
    async with await get_db() as db:
        result = await db.execute(
            select(workspace_col, func.count(Chat.id), func.max(Chat.updated_at))
            .where(
                Chat.meta["internal"].as_boolean().is_not(True),
                Chat.meta["subagent"].as_boolean().is_not(True),
                Chat.updated_at >= since_ms,
            )
            .group_by(workspace_col)
        )
        rows = result.all()
    return {
        path: {"chats": int(count or 0), "last_seen": int(last or 0)}
        for path, count, last in rows
        if path
    }


async def _live_jobs_by_workspace() -> dict[str, int]:
    """Outstanding (not settled) tasks per workspace, ignoring run rows.

    A ``run`` is an execution of a task, so counting them would make a
    workspace with a frequently-scheduled task look busier than one holding the
    task itself. Outstanding work is the signal.
    """
    async with await get_db() as db:
        result = await db.execute(
            select(Job.workspace, func.count(Job.id))
            .where(Job.kind != KIND_RUN, Job.status.in_(LIVE_JOB_STATUSES))
            .group_by(Job.workspace)
        )
        rows = result.all()
    return {
        workspace: int(count or 0) for workspace, count in rows if workspace
    }


async def rank_workspaces(paths: list[str]) -> list[WorkspaceActivity]:
    """Score every path given and return them best-first.

    A workspace with no recorded activity scores 0 rather than being dropped:
    it still belongs in the sidebar, just at the bottom.
    """
    unique = list(dict.fromkeys(p for p in paths if p))
    now = float(now_ms())
    since_ms = now - WINDOW_MS
    dwell = await _dwell_by_workspace(since_ms)
    chats = await _chat_activity(since_ms)
    jobs = await _live_jobs_by_workspace()

    unread = await _unread_counts(unique)

    ranked: list[WorkspaceActivity] = []
    for path in unique:
        dwell_row = dwell.get(path, {})
        chat_row = chats.get(path, {})
        live_jobs = jobs.get(path, 0)
        unread_count = int(unread.get(path, 0))
        activity = WorkspaceActivity(
            path=path,
            dwell_seconds=dwell_row.get("seconds", 0.0),
            active_days=dwell_row.get("days", 0),
            chats=chat_row.get("chats", 0),
            live_jobs=live_jobs,
            unread=unread_count,
            last_seen_ms=max(
                int(dwell_row.get("last_seen", 0)), int(chat_row.get("last_seen", 0))
            ),
        )
        if unread_count > 0:
            activity.reasons.append("unread")
        if live_jobs > 0:
            activity.reasons.append("tasks")
        activity.score = score_workspace(activity, now)
        ranked.append(activity)

    # Ties are broken by path so the order is deterministic across requests:
    # an unstable sort would reshuffle rows on every refetch.
    ranked.sort(key=lambda a: (-a.score, a.path))
    return ranked


def score_workspace(activity: WorkspaceActivity, now: float | None = None) -> float:
    """Weighted 0..100 priority for one workspace, stored on it."""
    now = now if now is not None else float(now_ms())
    recency = (
        _decay(now - activity.last_seen_ms, RECENCY_HALF_LIFE_MS)
        if activity.last_seen_ms
        else 0.0
    )
    activity.score = round(
        100.0
        * (
            WEIGHT_DWELL * _saturate(activity.dwell_seconds, DWELL_HALF)
            + WEIGHT_DAYS * _saturate(float(activity.active_days), DAYS_HALF)
            + WEIGHT_RECENCY * recency
            + WEIGHT_CHATS * _saturate(float(activity.chats), CHATS_HALF)
            + WEIGHT_JOBS * _saturate(float(activity.live_jobs), JOBS_HALF)
        ),
        2,
    )
    return activity.score


async def _unread_counts(paths: list[str]) -> dict[str, int]:
    """Unread chats per workspace, for the paths in question.

    Reads them back from the ranking query rather than persisting a second read
    state, matching ``Chat.unread_counts_by_workspace``'s rule exactly.
    """
    if not paths:
        return {}
    workspace_col = Chat.meta["workspace"].as_string()
    async with await get_db() as db:
        result = await db.execute(
            select(workspace_col, func.count(Chat.id))
            .where(
                workspace_col.in_(list(dict.fromkeys(paths))),
                Chat.meta["internal"].as_boolean().is_not(True),
                Chat.meta["subagent"].as_boolean().is_not(True),
                Chat.updated_at > func.coalesce(Chat.last_read_at, 0),
            )
            .group_by(workspace_col)
        )
        rows = result.all()
    return {path: int(count or 0) for path, count in rows if path}


async def workspace_priority(
    paths: list[str], current: str | None = None, limit: int = 8
) -> dict:
    """Ranked workspaces plus the suggested visible count and hold list.

    ``pinned`` is the set that should stay visible whatever the score says —
    unread chats, outstanding tasks, and the workspace the user is in right
    now. The frontend uses it to seed its initial state, then remembers what
    the user chose, because a ranking is only a good suggestion once.
    """
    ranked = await rank_workspaces(paths)
    current_path = _resolve(current)
    if current_path:
        for item in ranked:
            if item.path == current_path and "current" not in item.reasons:
                item.reasons.append("current")

    held = [item.path for item in ranked if item.reasons]
    return {
        "window_days": WINDOW_DAYS,
        "limit": limit,
        "pinned": held,
        "workspaces": [
            {
                "path": item.path,
                "score": item.score,
                "dwell_seconds": round(item.dwell_seconds, 1),
                "active_days": item.active_days,
                "chats": item.chats,
                "live_jobs": item.live_jobs,
                "unread": item.unread,
                "last_seen_ms": item.last_seen_ms,
                "reasons": list(item.reasons),
            }
            for item in ranked
        ],
    }


def _resolve(path: str | None) -> str | None:
    """Normalise a path the way the sidebar stores it, or drop it."""
    if not path:
        return None
    from pathlib import Path

    try:
        expanded = Path(path).expanduser()
        return str(expanded.resolve()) if expanded.is_absolute() else None
    except (OSError, RuntimeError, ValueError):
        return None