"""Client UI performance metrics: ingestion + analysis.

The frontend batches timing samples (tab switches, folder navigation, heavy
mounts, browser long tasks) and POSTs them to ``/api/ui-events``; they land in
the ``ui_events`` table so we can see which interactions are slow and whether
it degrades as more tabs are open. ``/summary`` returns p50/p95/max per event
type for a quick read.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from cptr.models.ui_events import UiEvent
from cptr.utils.config import get_or_create_user, now_ms

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ui-events", tags=["ui-events"])

# Guardrail: a single batch should never be larger than this. The client caps
# its buffer well below it; anything bigger is a bug or abuse.
MAX_EVENTS_PER_BATCH = 200
DEFAULT_WINDOW_HOURS = 24


class UiEventIn(BaseModel):
    kind: str
    label: Optional[str] = None
    # Wall-clock epoch ms (real timeline). Legacy clients sent perf.now().
    ts: float = 0.0
    # Monotonic page-relative ms, for intra-page gap analysis.
    perf_ms: Optional[float] = None
    duration_ms: float = 0.0
    # Per-event workspace: captured at record time, so navigating workspaces
    # mid-batch can't mislabel a sample. Falls back to the batch value.
    workspace: Optional[str] = None
    meta: Optional[dict[str, Any]] = None


class UiEventBatch(BaseModel):
    session_id: Optional[str] = None
    workspace: Optional[str] = None
    events: list[UiEventIn] = Field(default_factory=list)


async def _user_id(request: Request) -> str | None:
    auth = getattr(request.state, "auth", None)
    username = getattr(auth, "username", None) if auth else None
    if not username:
        return None
    try:
        return await get_or_create_user(username)
    except Exception:
        logger.debug("ui-events: could not resolve user", exc_info=True)
        return None


@router.post("")
async def ingest(request: Request, batch: UiEventBatch):
    """Accept a batch of client timing samples."""
    if not batch.events:
        return {"status": "empty", "stored": 0}

    events = batch.events[:MAX_EVENTS_PER_BATCH]
    user_id = await _user_id(request)
    rows = [
        {
            "user_id": user_id,
            "workspace": event.workspace or batch.workspace,
            "session_id": batch.session_id,
            "kind": event.kind,
            "label": event.label,
            "ts": event.ts,
            "perf_ms": event.perf_ms,
            "duration_ms": event.duration_ms,
            "meta": event.meta,
        }
        for event in events
    ]
    stored = await UiEvent.bulk_create(rows, created_at=now_ms())
    return {"status": "ok", "stored": stored}


@router.get("/summary")
async def summary(
    request: Request,
    since_ms: int = Query(0, description="Include events at/after this epoch-ms. 0 = last 24h."),
    kind: Optional[str] = Query(None, description="Restrict to a single event kind"),
):
    """Aggregated durations per (kind, label), slowest p95 first."""
    if since_ms <= 0:
        since_ms = now_ms() - DEFAULT_WINDOW_HOURS * 60 * 60 * 1000
    groups = await UiEvent.summary(since_ms, kind=kind)
    return {"since_ms": since_ms, "groups": groups}


@router.get("/recent")
async def recent(
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    kind: Optional[str] = Query(None),
):
    """Raw event samples, newest first."""
    rows = await UiEvent.recent(limit=limit, kind=kind)
    return {
        "events": [
            {
                "id": row.id,
                "created_at": row.created_at,
                "ts": row.ts,
                "perf_ms": row.perf_ms,
                "kind": row.kind,
                "label": row.label,
                "duration_ms": row.duration_ms,
                "workspace": row.workspace,
                "session_id": row.session_id,
                "meta": row.meta,
            }
            for row in rows
        ]
    }


@router.post("/prune")
async def prune(
    request: Request,
    older_than_days: int = Query(7, ge=1, le=365),
):
    """Drop events older than N days."""
    cutoff = now_ms() - older_than_days * 24 * 60 * 60 * 1000
    deleted = await UiEvent.prune(cutoff)
    return {"status": "pruned", "deleted": deleted, "cutoff_ms": cutoff}


@router.delete("")
async def clear(request: Request):
    """Delete every stored event."""
    deleted = await UiEvent.clear()
    return {"status": "cleared", "deleted": deleted}
