"""Client-reported UI performance events.

The frontend measures the interactions that make the app feel slow — tab
switches, workspace/directory navigation, heavy component mounts — plus the
browser's own long-task entries, and ships them here in small batches. Rows are
append-only and cheap to write; read them back via ``summary``/``recent`` to
work out which interactions are slow and how that degrades as tabs pile up.
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, Column, Float, Index, Text, delete, select
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
    # performance.now() on the client: monotonic, only comparable within a session.
    ts = Column(Float, nullable=False)
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
