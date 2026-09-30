"""Automation utilities: RRULE helpers, execution.

There is no scheduler worker here any more. Automations are recurring rows in
`jobs` (migration 0010) and `cptr.utils.task_scheduler` is the one loop; the old
`scheduler_worker_loop` survives only as a refusal, so a stale caller cannot
quietly start a second scheduler. `execute_automation` still does what a run
means: create a real chat and drive it with `start_task`.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from dateutil.rrule import rrulestr

logger = logging.getLogger(__name__)


####################
# RRULE Helpers
####################


def _freq_of(s: str) -> str:
    raw = s.replace("RRULE:", "")
    parts = dict(p.split("=", 1) for p in raw.split(";") if "=" in p)
    return parts.get("FREQ", "")


def _parse_rule(s: str):
    """Parse RRULE with clock-aligned DTSTART for sub-daily frequencies.

    MINUTELY/HOURLY rules are anchored to **today's midnight** rather than a
    fixed epoch so intervals still snap to clock boundaries (every 5min = :00,
    :05, :10) — midnight is midnight in both cases, so an interval that divides
    a day aligns identically. The anchor has to move because ``after(now)``
    walks forward from DTSTART one occurrence at a time: from 2000-01-01, a
    plain ``FREQ=MINUTELY`` meant ~14 million steps, which does not finish.
    (Found live: one such row wedged the task scheduler, since the tick calls
    this to advance a template's ``trigger_at``.)
    """
    if _freq_of(s) in ("MINUTELY", "HOURLY"):
        anchor = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return rrulestr(s, dtstart=anchor, ignoretz=True)
    return rrulestr(s, ignoretz=True)


def validate_rrule(s: str) -> None:
    """Raise ValueError if the RRULE is malformed or exhausted."""
    try:
        rule = _parse_rule(s)
    except Exception as e:
        raise ValueError(f"Invalid RRULE: {e}")
    now = datetime.now()
    if rule.after(now) is None:
        raise ValueError("RRULE has no future occurrences")


def next_run_ns(s: str) -> Optional[int]:
    """Next occurrence as epoch nanoseconds."""
    now = datetime.now()
    dt = _parse_rule(s).after(now)
    if dt is None:
        return None
    return int(dt.timestamp() * 1_000_000_000)


def next_n_runs_ns(s: str, n: int = 5) -> list[int]:
    """Compute next N occurrences for UI preview."""
    rule = _parse_rule(s)
    result = []
    dt = datetime.now()
    for _ in range(n):
        dt = rule.after(dt)
        if not dt:
            break
        result.append(int(dt.timestamp() * 1_000_000_000))
    return result


####################
# Scheduler Worker
####################


async def scheduler_worker_loop(app) -> None:
    """Removed: one scheduler, in ``cptr.utils.task_scheduler``.

    Automations are recurring tasks in `jobs` as of migration 0010, so this loop
    would race the real one and fire every template twice. It refuses to start
    rather than doing that quietly — if a stale ``app.py`` or an unrestarted
    lane still calls it, the log says so.
    """
    from cptr.utils.task_scheduler import task_scheduler_loop

    logger.error(
        "scheduler_worker_loop is gone (see cptr/utils/task_scheduler.py); "
        "not starting a second scheduler. Run task_scheduler_loop instead."
    )
    del app, task_scheduler_loop


####################
# Execute
####################


async def execute_automation(app, automation, webhook_payload: str | None = None) -> None:
    """Deprecated: an automation is a recurring task in `jobs` now.

    Kept so a stale caller (a lane that has not been restarted, a saved webhook
    handler) still lands on the one execution path instead of writing a rival
    chat. The automation's id is its job's id — migration 0010 preserves it,
    which is also why existing webhook URLs keep resolving.
    """
    from cptr.models.jobs import Job
    from cptr.utils.task_scheduler import run_task_now

    logger.warning(
        "execute_automation is deprecated; queuing %s as a task run", automation.id[:8]
    )
    job = await Job.get_by_id(automation.id)
    if job is None:
        raise RuntimeError(
            f"automation {automation.id} has no jobs row; run the 0010 migration"
        )
    await run_task_now(app, job, webhook_payload=webhook_payload)
