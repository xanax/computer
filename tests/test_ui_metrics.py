"""Tests for the UI perf/dwell aggregations.

Two things are pinned here, and both came out of reading real telemetry rather
than from the arithmetic:

1. The throttled mask. A background tab's rAFs are paused, so a sample taken
   while hidden records how long the tab sat in the background, not how long a
   paint took. Before the mask, `mount` p95 read as 388 seconds; the real value
   is ~345 ms. A summary that quietly lets those rows back in is worse than one
   with no summary at all, because it looks authoritative.
2. Fragmentation. Dwell spans already existed and their length distribution was
   never read, so the shape of the attention was available and unclaimed.

Design: .agent-kb/areas/ui-metrics-telemetry.md
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cptr.models.base import Base
from cptr.models.ui_events import UiEvent, _is_throttled
from cptr.utils import db as dbmod


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def ui_db(tmp_path, monkeypatch):
    """Point cptr's async session factory at a fresh SQLite file."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'ui-events.db'}", poolclass=NullPool
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


async def _seed(rows: list[dict]) -> None:
    await UiEvent.bulk_create(rows, created_at=1_760_000_000_000)


# -- The throttled predicate ---------------------------------------


def test_is_throttled_reads_the_new_flag_and_the_ones_that_predate_it():
    assert _is_throttled({"throttled": True})
    assert _is_throttled({"started_hidden": 1})
    assert _is_throttled({"hidden_at_paint": True})
    # A visible tab that merely focused=false is not throttled: the document was
    # on screen, the window just was not frontmost.
    assert not _is_throttled({"focused": False, "hidden": False})
    assert not _is_throttled({"throttled": False, "started_hidden": 0})
    assert not _is_throttled(None)
    assert not _is_throttled({})


# -- summary() masking ---------------------------------------------


def test_summary_excludes_background_tabs_and_counts_them(ui_db):
    rows = [
        # Four honest, visible mounts.
        *[
            {
                "kind": "mount",
                "label": "chat",
                "ts": 1_760_000_000_000.0 + i,
                "duration_ms": 100.0,
                "meta": {"started_hidden": False},
            }
            for i in range(4)
        ],
        # One paused-rAF monster, as the browser actually reports it.
        {
            "kind": "mount",
            "label": "chat",
            "ts": 1_760_000_000_100.0,
            "duration_ms": 500_000.0,
            "meta": {"throttled": True, "started_hidden": True},
        },
    ]
    _run(_seed(rows))

    masked = _run(UiEvent.summary(1_760_000_000_000, kind="mount"))
    assert len(masked) == 1
    group = masked[0]
    assert group["count"] == 4
    assert group["max"] == 100.0
    # The 500-second row is counted as excluded, not silently dropped.
    assert group["throttled"] == 1

    raw = _run(UiEvent.summary(1_760_000_000_000, kind="mount", include_throttled=True))
    assert raw[0]["count"] == 5
    assert raw[0]["max"] == 500_000.0
    # `throttled` keeps reporting the background-tab count even when they are
    # folded back in -- that is what makes the raw view auditable rather than a
    # second, unexplained set of numbers.
    assert raw[0]["throttled"] == 1


def test_summary_mask_works_on_rows_written_before_the_flag_existed(ui_db):
    """The fallback is what stops the mask being a no-op on existing data."""
    _run(
        _seed(
            [
                {
                    "kind": "mount",
                    "label": "files",
                    "ts": 1_760_000_000_000.0,
                    "duration_ms": 200.0,
                    "meta": {"started_hidden": False},
                },
                {
                    "kind": "mount",
                    "label": "files",
                    "ts": 1_760_000_000_001.0,
                    "duration_ms": 4_000_000.0,
                    "meta": {"started_hidden": True},  # no `throttled` key
                },
            ]
        )
    )
    masked = _run(UiEvent.summary(1_760_000_000_000, kind="mount"))
    assert masked[0]["count"] == 1
    assert masked[0]["max"] == 200.0
    assert masked[0]["throttled"] == 1


def test_scan_returns_meta_so_readers_can_mask(ui_db):
    """`ui_metrics(metric="slow")` filters on meta; without this it is a no-op."""
    _run(
        _seed(
            [
                {
                    "kind": "mount",
                    "label": "chat",
                    "ts": 1_760_000_000_000.0,
                    "duration_ms": 100.0,
                    "meta": {"throttled": True},
                }
            ]
        )
    )
    rows = _run(UiEvent.scan())
    assert rows[0]["meta"] == {"throttled": True}
    assert _is_throttled(rows[0]["meta"])


# -- Fragmentation -------------------------------------------------


def test_fragmentation_reconciles_with_the_dwell_spans_it_reads(ui_db):
    _run(
        _seed(
            [
                {
                    "kind": "dwell",
                    "workspace": "/a",
                    "label": "switch",
                    "ts": 1_760_000_000_000.0,
                    "duration_ms": 60_000.0,
                },
                {
                    "kind": "dwell",
                    "workspace": "/a",
                    "label": "switch",
                    "ts": 1_760_000_060_000.0,
                    "duration_ms": 600_000.0,
                },
                {
                    "kind": "dwell",
                    "workspace": "/b",
                    # Closed by the tab going to the background, not a switch.
                    "label": "hidden",
                    "ts": 1_760_000_660_000.0,
                    "duration_ms": 900_000.0,
                },
            ]
        )
    )
    stats = _run(UiEvent.fragmentation(1_760_000_000))

    assert stats["span_count"] == 3
    assert stats["total_seconds"] == pytest.approx(1560.0)
    # Only the two spans closed by a workspace change count as switches.
    assert stats["switch_count"] == 2
    assert stats["median_span_seconds"] == pytest.approx(600.0)
    assert stats["longest_span_seconds"] == pytest.approx(900.0)
    # 600 s and 900 s are both >= 5 min; the 60 s span is not.
    assert stats["focus_ratio_pct"] == pytest.approx(100.0 * 1500 / 1560, abs=0.1)

    # Bucket shares must reconstruct the whole, with nothing double-counted.
    assert sum(b["count"] for b in stats["buckets"]) == 3
    assert sum(b["seconds"] for b in stats["buckets"]) == pytest.approx(1560.0)
    assert sum(b["share_pct"] for b in stats["buckets"]) == pytest.approx(100.0, abs=0.2)

    by_ws = {w["workspace"]: w for w in stats["per_workspace"]}
    assert by_ws["/a"]["spans"] == 2
    assert by_ws["/a"]["seconds"] == pytest.approx(660.0)
    # Linear-interpolated median of [60 s, 600 s] is 330 s, not the smaller of
    # the two. Pinned so the interpolation is not "corrected" later.
    assert by_ws["/a"]["median_span_seconds"] == pytest.approx(330.0)
    assert by_ws["/b"]["seconds"] == pytest.approx(900.0)


def test_fragmentation_separates_many_short_visits_from_few_long_ones(ui_db):
    _run(
        _seed(
            [
                {
                    "kind": "dwell",
                    "workspace": "/churn",
                    "label": "switch",
                    "ts": 1_760_000_000_000.0 + i * 30_000.0,
                    "duration_ms": 150_000.0,
                }
                for i in range(20)
            ]
            + [
                {
                    "kind": "dwell",
                    "workspace": "/deep",
                    "label": "switch",
                    "ts": 1_760_000_000_000.0 + i * 1_800_000.0,
                    "duration_ms": 1_500_000.0,
                }
                for i in range(2)
            ]
        )
    )
    stats = _run(UiEvent.fragmentation(1_760_000_000_000))
    by_ws = {w["workspace"]: w for w in stats["per_workspace"]}

    assert by_ws["/churn"]["spans"] == 20
    assert by_ws["/churn"]["median_span_seconds"] == pytest.approx(150.0)
    assert by_ws["/deep"]["spans"] == 2
    assert by_ws["/deep"]["median_span_seconds"] == pytest.approx(1500.0)
    # Two workspaces, equal time, opposite shapes: 20 x 150 s against 2 x 1500 s
    # is 3000 s either way. Totals alone cannot tell them apart, which is the
    # reason this metric exists.
    assert by_ws["/churn"]["seconds"] == pytest.approx(3000.0)
    assert by_ws["/deep"]["seconds"] == pytest.approx(3000.0)
    # Focus ratio separates them even though the totals cannot: the 150 s churn
    # spans sit under the 5-minute cut, so only the 3000 s of deep work counts.
    assert stats["focus_ratio_pct"] == pytest.approx(50.0)


def test_fragmentation_ignores_legacy_rows_and_empty_windows(ui_db):
    _run(
        _seed(
            [
                # Pre-migration sample: page-relative perf.now(), not epoch-ms.
                {"kind": "dwell", "workspace": "/old", "ts": 1234.0, "duration_ms": 60_000.0},
                {
                    "kind": "dwell",
                    "workspace": "/now",
                    "ts": 1_760_000_000_000.0,
                    "duration_ms": 60_000.0,
                },
            ]
        )
    )
    stats = _run(UiEvent.fragmentation(1_760_000_000_000))
    assert stats["span_count"] == 1
    assert [w["workspace"] for w in stats["per_workspace"]] == ["/now"]

    empty = _run(UiEvent.fragmentation(1_760_000_000_000_000))
    assert empty["span_count"] == 0
    assert empty["buckets"] == []
    assert empty["per_workspace"] == []
    assert empty["focus_ratio_pct"] == 0.0
