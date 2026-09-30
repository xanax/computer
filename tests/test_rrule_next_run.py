"""Tests for the RRULE helper the task scheduler advances templates with.

`Job.promote_due_templates` calls `next_run_ns` on every recurring task that has
fallen due, on every tick, so this has to be *fast* as well as right: a call that
does not return stops scheduling for the whole server, and no exception is raised
to be caught. Both halves of that are tested here.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

from cptr.utils.automations import next_n_runs_ns, next_run_ns, validate_rrule


def _dt(ns: int) -> datetime:
    return datetime.fromtimestamp(ns / 1_000_000_000)


def test_every_minute_returns_promptly():
    """`FREQ=MINUTELY` used to walk from the year 2000 — millions of steps.

    The rule is anchored to today's midnight instead, so the search is bounded
    by one day's worth of occurrences. Anything slower than ~1s means the anchor
    has drifted back to a fixed epoch.
    """
    started = time.time()
    ns = next_run_ns("FREQ=MINUTELY")
    elapsed = time.time() - started

    assert ns is not None
    assert elapsed < 1.0, f"next_run_ns took {elapsed:.1f}s"
    assert _dt(ns) > datetime.now()


def test_sub_daily_rules_stay_on_the_clock():
    """Anchoring must not cost the clock alignment the old code was for."""
    every_five = _dt(next_run_ns("FREQ=MINUTELY;INTERVAL=5"))
    assert every_five.second == 0
    assert every_five.minute % 5 == 0

    every_two_hours = _dt(next_run_ns("FREQ=HOURLY;INTERVAL=2"))
    assert every_two_hours.minute == 0
    assert every_two_hours.hour % 2 == 0


def test_daily_rules_keep_their_time_of_day():
    """The common case: a schedule pinned to a wall-clock time."""
    ns = next_run_ns("FREQ=DAILY;BYHOUR=3;BYMINUTE=30;BYSECOND=0")
    assert ns is not None
    when = _dt(ns)
    assert (when.hour, when.minute) == (3, 30)
    assert when > datetime.now()
    assert when - datetime.now() <= timedelta(days=1)


def test_an_exhausted_rule_has_no_next_run():
    """What makes the scheduler retire a template instead of spinning on it."""
    assert next_run_ns("DTSTART:20200101T070000Z\nRRULE:FREQ=DAILY;COUNT=1") is None


def test_n_runs_preview_is_ordered_and_spaced():
    runs = next_n_runs_ns("FREQ=MINUTELY;INTERVAL=15", 3)
    assert len(runs) == 3
    assert runs == sorted(runs)
    assert [_dt(r).minute % 15 for r in runs] == [0, 0, 0]


def test_validate_rejects_junk_and_exhausted_rules():
    import pytest

    validate_rrule("FREQ=DAILY")  # no raise
    with pytest.raises(ValueError):
        validate_rrule("FREQ=")
    with pytest.raises(ValueError):
        validate_rrule("DTSTART:20200101T070000Z\nRRULE:FREQ=DAILY;COUNT=1")
