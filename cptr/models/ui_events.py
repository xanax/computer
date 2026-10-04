"""Client-reported UI performance events.

The frontend measures the interactions that make the app feel slow — tab
switches, workspace/directory navigation, heavy component mounts — plus the
browser's own long-task entries, and ships them here in small batches. Rows are
append-only and cheap to write; read them back via ``summary``/``recent`` to
work out which interactions are slow and how that degrades as tabs pile up.
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, Column, Float, Index, Text, delete, func, select
from sqlalchemy.dialects.sqlite import JSON

from cptr.models.base import Base
from cptr.utils.db import get_db


def _uuid() -> str:
    return str(uuid.uuid4())


def _is_throttled(meta: dict | None) -> bool:
    """True when a sample's duration is background time, not latency.

    ``measureToPaint`` stamps ``throttled`` (and the two flags it derives from)
    on every new sample. Rows written before that change only carry
    ``started_hidden``, so the fallbacks matter: without them the mask silently
    does nothing on existing data and the tail stays garbage. Same predicate the
    ``ui_metrics`` tool uses, kept in one place on purpose.
    """
    if not meta:
        return False
    return bool(meta.get("throttled") or meta.get("started_hidden") or meta.get("hidden_at_paint"))


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Linear-interpolated percentile over an ascending list."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return round(sorted_values[0], 1)
    rank = (len(sorted_values) - 1) * (pct / 100.0)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = rank - lo
    value = sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac
    return round(value, 1)


class UiEvent(Base):
    """A single client UI timing sample."""

    __tablename__ = "ui_events"

    id = Column(Text, primary_key=True, default=_uuid)
    user_id = Column(Text, nullable=True, index=True)
    workspace = Column(Text, nullable=True)
    session_id = Column(Text, nullable=True, index=True)
    # Event family: "tab_switch", "dir_list", "dir_navigate", "mount", "long_task", ...
    kind = Column(Text, nullable=False)
    # Short label for the thing measured (tab type, component name, path, ...).
    label = Column(Text, nullable=True)
    # Client wall-clock ms since epoch — a real timeline that survives reloads
    # and lets events be ordered across sessions. (Legacy rows written before
    # 0006 hold page-relative performance.now() values here instead.)
    ts = Column(Float, nullable=False)
    # performance.now() at record time: monotonic per page load, used for precise
    # intra-page gaps that are immune to clock adjustments. Null on legacy rows.
    perf_ms = Column(Float, nullable=True)
    duration_ms = Column(Float, nullable=False)
    # Free-form context: tab counts, entry counts, cache hit/miss, ...
    meta = Column(JSON, nullable=True)
    # Server wall-clock ms since epoch, for grouping by day / retention.
    created_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index("ix_ui_event_kind_created", "kind", "created_at"),
        Index("ix_ui_event_label_created", "label", "created_at"),
    )

    # ── Class methods ────────────────────────────────────────

    @staticmethod
    async def bulk_create(rows: list[dict], created_at: int) -> int:
        """Insert a batch of event dicts in a single transaction."""
        if not rows:
            return 0
        objects = [
            UiEvent(
                user_id=row.get("user_id"),
                workspace=row.get("workspace"),
                session_id=row.get("session_id"),
                kind=row.get("kind") or "unknown",
                label=row.get("label"),
                ts=float(row.get("ts") or 0.0),
                perf_ms=(float(row["perf_ms"]) if row.get("perf_ms") is not None else None),
                duration_ms=float(row.get("duration_ms") or 0.0),
                meta=row.get("meta"),
                created_at=created_at,
            )
            for row in rows
        ]
        async with await get_db() as db:
            db.add_all(objects)
            await db.commit()
        return len(objects)

    @staticmethod
    async def recent(limit: int = 100, kind: str | None = None) -> list[UiEvent]:
        async with await get_db() as db:
            stmt = select(UiEvent).order_by(UiEvent.created_at.desc(), UiEvent.ts.desc())
            if kind:
                stmt = stmt.where(UiEvent.kind == kind)
            result = await db.execute(stmt.limit(limit))
            return list(result.scalars().all())

    @staticmethod
    async def summary(
        since_ms: int,
        kind: str | None = None,
        max_rows: int = 50_000,
        include_throttled: bool = False,
    ) -> list[dict]:
        """Aggregate durations per (kind, label) with p50/p95/max/mean.

        ``include_throttled`` controls background-tab samples. The client marks
        them ``meta.throttled`` (see ``measureToPaint`` in ``utils/perf.ts``)
        because rAF callbacks pause while a document is hidden: the duration
        recorded is time the tab spent in the background, not latency. They
        are excluded by default so p95/max mean what the column says. Pass
        True to see the raw numbers; ``throttled`` then reports how many
        samples were set aside.
        """
        async with await get_db() as db:
            stmt = select(UiEvent.kind, UiEvent.label, UiEvent.duration_ms, UiEvent.meta).where(
                UiEvent.created_at >= since_ms
            )
            if kind:
                stmt = stmt.where(UiEvent.kind == kind)
            # Newest first, capped so a runaway client can't blow up memory.
            result = await db.execute(stmt.order_by(UiEvent.created_at.desc()).limit(max_rows))
            rows = result.all()

        grouped: dict[tuple[str, str | None], list[float]] = {}
        throttled_counts: dict[tuple[str, str | None], int] = {}
        for row_kind, label, duration, meta in rows:
            is_throttled = _is_throttled(meta)
            key = (row_kind, label)
            if is_throttled:
                throttled_counts[key] = throttled_counts.get(key, 0) + 1
                if not include_throttled:
                    continue
            grouped.setdefault(key, []).append(float(duration or 0.0))

        out: list[dict] = []
        for (group_kind, label), durations in grouped.items():
            durations.sort()
            total = len(durations)
            out.append(
                {
                    "kind": group_kind,
                    "label": label,
                    "count": total,
                    "p50": _percentile(durations, 50),
                    "p95": _percentile(durations, 95),
                    "max": round(durations[-1], 1),
                    "mean": round(sum(durations) / total, 1),
                    # How many background-tab samples were excluded (0 when
                    # include_throttled is on, or none existed).
                    "throttled": throttled_counts.get((group_kind, label), 0),
                }
            )
        # Slowest tail first — that's what we want to fix.
        out.sort(key=lambda item: (-item["p95"], item["kind"], item["label"] or ""))
        return out

    @staticmethod
    async def scan(since_ms: int = 0, limit: int = 200_000) -> list[dict]:
        """Raw samples ordered by event time, for offline analysis.

        Deliberately ordered and filtered on ``ts`` (the client's wall clock)
        rather than ``created_at`` (server receive time): batches are delivered
        on a timer and on unload, so receive order scrambles the real sequence
        and would misattribute every gap. Rows whose ``ts`` is not epoch-ms are
        legacy samples written before migration 0006 and are skipped — they
        can't be placed on a timeline at all.
        """
        async with await get_db() as db:
            stmt = (
                select(
                    UiEvent.ts,
                    UiEvent.kind,
                    UiEvent.label,
                    UiEvent.workspace,
                    UiEvent.session_id,
                    UiEvent.duration_ms,
                    # meta carries the ambient context and the throttled flag,
                    # so a reader can mask background-tab samples. Needed by
                    # `ui_metrics(metric="slow")` — without it the mask is a
                    # no-op and every background pane re-enters the p95.
                    UiEvent.meta,
                )
                .where(UiEvent.ts > 1e12)
                .order_by(UiEvent.ts.asc())
                .limit(limit)
            )
            if since_ms > 0:
                stmt = stmt.where(UiEvent.ts >= since_ms)
            result = await db.execute(stmt)
            return [
                {
                    "ts": row[0],
                    "kind": row[1],
                    "label": row[2],
                    "workspace": row[3],
                    "session_id": row[4],
                    "duration_ms": row[5],
                    "meta": row[6],
                }
                for row in result.all()
            ]

    @staticmethod
    async def dwell_by_workspace(since_ms: int = 0) -> tuple[dict[str, float], float]:
        """Active seconds per workspace, from client-reported ``dwell`` spans.

        Returns ``(per_workspace_seconds, total_seconds)``.

        Only measured dwell spans count. There is deliberately no
        gap-attribution fallback here: this feeds a number displayed next to a
        workspace name, and a displayed figure should be measured, not
        estimated. With no samples the caller gets zeros and shows nothing.
        """
        async with await get_db() as db:
            stmt = select(UiEvent.workspace, func.sum(UiEvent.duration_ms)).where(
                UiEvent.kind == "dwell",
                UiEvent.ts > 1e12,
            )
            if since_ms > 0:
                stmt = stmt.where(UiEvent.ts >= since_ms)
            result = await db.execute(stmt.group_by(UiEvent.workspace))
            rows = result.all()

        per_workspace: dict[str, float] = {}
        for workspace, duration_ms in rows:
            if duration_ms:
                per_workspace[workspace or "(unknown)"] = float(duration_ms) / 1000.0
        return per_workspace, sum(per_workspace.values())

    # Dwell span length boundaries, in ms. A span is one uninterrupted stretch
    # of visible time in one workspace: a workspace switch or a hidden document
    # closes it. So the *distribution* of span lengths reads as how fragmented
    # the session was, and it is measured rather than inferred — the same
    # honesty rule `dwell_by_workspace` follows.
    FRAGMENT_BUCKETS: tuple[tuple[str, int], ...] = (
        ("under_1m", 60_000),
        ("1_5m", 300_000),
        ("5_15m", 900_000),
        ("15_30m", 1_800_000),
    )

    @staticmethod
    async def fragmentation(since_ms: int) -> dict:
        """Context-switching picture for a window, from measured dwell spans.

        Answers "was this time well spent" only in the sense the data actually
        supports: how long the unbroken stretches of attention were, and how
        often they were broken. It says nothing about whether the work was any
        good — there is no outcome signal to join against (see area notes).

        ``per_workspace`` carries each workspace's own median span and
        switch-in count, so a workspace that attracts many short visits is
        distinguishable from one holding a few long ones.
        """
        async with await get_db() as db:
            stmt = select(
                UiEvent.duration_ms, UiEvent.workspace, UiEvent.label, UiEvent.session_id
            ).where(UiEvent.kind == "dwell", UiEvent.ts > 1e12)
            if since_ms > 0:
                stmt = stmt.where(UiEvent.ts >= since_ms)
            result = await db.execute(stmt.order_by(UiEvent.ts.asc()))
            rows = result.all()

        spans: list[tuple[float, str, str | None]] = [
            (float(d or 0.0), ws or "(unknown)", sid) for d, ws, _label, sid in rows
        ]
        if not spans:
            return {
                "span_count": 0,
                "total_seconds": 0.0,
                "median_span_seconds": 0.0,
                "mean_span_seconds": 0.0,
                "focus_ratio_pct": 0.0,
                "switch_count": 0,
                "buckets": [],
                "per_workspace": [],
            }

        durations = sorted(d for d, _ws, _sid in spans)
        total_ms = sum(durations)
        # A "switch" is a span closed because the workspace changed, not one
        # closed by the tab going to the background — the label records which.
        switch_count = sum(1 for _d, _ws, label, _sid in rows if label == "switch")

        buckets = []
        remaining = list(durations)
        for name, upper in UiEvent.FRAGMENT_BUCKETS:
            count = sum(1 for d in remaining if d < upper)
            share_ms = sum(d for d in remaining if d < upper)
            remaining = [d for d in remaining if d >= upper]
            buckets.append(
                {
                    "bucket": name,
                    "under_ms": upper,
                    "count": count,
                    "seconds": round(share_ms / 1000.0, 1),
                    "share_pct": round(100.0 * share_ms / total_ms, 1) if total_ms else 0.0,
                }
            )
        buckets.append(
            {
                "bucket": "30m_plus",
                "under_ms": None,
                "count": len(remaining),
                "seconds": round(sum(remaining) / 1000.0, 1),
                "share_pct": round(100.0 * sum(remaining) / total_ms, 1) if total_ms else 0.0,
            }
        )

        per_ws: dict[str, list[float]] = {}
        ws_sessions: dict[str, set] = {}
        for duration, workspace, sid in spans:
            per_ws.setdefault(workspace, []).append(duration)
            if sid:
                ws_sessions.setdefault(workspace, set()).add(sid)

        # Focus ratio: the share of tracked time that sat inside spans long
        # enough to be real attention. The 5-minute cut is a judgement call, and
        # it is the one number here that is not purely arithmetic — see the
        # Decisions note in the KB area before changing it.
        long_enough = [d for d in durations if d >= 300_000]
        focus_ms = sum(long_enough)

        return {
            "span_count": len(spans),
            "total_seconds": round(total_ms / 1000.0, 1),
            "median_span_seconds": round(_percentile(durations, 50) / 1000.0, 1),
            "mean_span_seconds": round(total_ms / len(durations) / 1000.0, 1),
            "longest_span_seconds": round(durations[-1] / 1000.0, 1),
            "focus_ratio_pct": round(100.0 * focus_ms / total_ms, 1) if total_ms else 0.0,
            "switch_count": switch_count,
            "buckets": buckets,
            "per_workspace": sorted(
                (
                    {
                        "workspace": workspace,
                        "spans": len(values),
                        "seconds": round(sum(values) / 1000.0, 1),
                        "median_span_seconds": round(_percentile(sorted(values), 50) / 1000.0, 1),
                        "sessions": len(ws_sessions.get(workspace, ())),
                    }
                    for workspace, values in per_ws.items()
                ),
                key=lambda item: -item["seconds"],
            ),
        }

    @staticmethod
    async def prune(before_ms: int) -> int:
        async with await get_db() as db:
            result = await db.execute(delete(UiEvent).where(UiEvent.created_at < before_ms))
            await db.commit()
            return result.rowcount or 0

    @staticmethod
    async def clear() -> int:
        async with await get_db() as db:
            result = await db.execute(delete(UiEvent))
            await db.commit()
            return result.rowcount or 0
