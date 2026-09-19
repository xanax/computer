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
    ) -> list[dict]:
        """Aggregate durations per (kind, label) with p50/p95/max/mean."""
        async with await get_db() as db:
            stmt = select(UiEvent.kind, UiEvent.label, UiEvent.duration_ms).where(
                UiEvent.created_at >= since_ms
            )
            if kind:
                stmt = stmt.where(UiEvent.kind == kind)
            # Newest first, capped so a runaway client can't blow up memory.
            result = await db.execute(stmt.order_by(UiEvent.created_at.desc()).limit(max_rows))
            rows = result.all()

        grouped: dict[tuple[str, str | None], list[float]] = {}
        for row_kind, label, duration in rows:
            grouped.setdefault((row_kind, label), []).append(float(duration or 0.0))

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
