"""Workspace model: one row per user+path, stores tabs/groups/layout."""

from __future__ import annotations

import os
import time
import uuid

from sqlalchemy import BigInteger, Column, ForeignKey, Text, UniqueConstraint, select, delete
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import relationship

from cptr.models.base import Base
from cptr.utils.db import get_db


def _uuid() -> str:
    return str(uuid.uuid4())


def normalize_path(path: str) -> str:
    """Absolute, symlink-free form of a workspace path ("" when unusable).

    Rows outlive the code that wrote them: older ones kept the path exactly as
    the client sent it, so `~`, a relative path and a symlinked prefix can all
    point at the same workspace. Comparing normalised forms is what makes a
    lookup by chat workspace path land on the row the dashboard edits.
    """
    raw = (path or "").strip()
    if not raw:
        return ""
    try:
        return os.path.realpath(os.path.expanduser(raw))
    except (OSError, ValueError):
        return ""


class Workspace(Base):
    """Per-workspace state. One row per (user, filesystem path)."""

    __tablename__ = "workspaces"

    id = Column(Text, primary_key=True, default=_uuid)
    user_id = Column(Text, ForeignKey("users.id"), nullable=False)
    path = Column(Text, nullable=False)
    name = Column(Text, nullable=False)
    data = Column(JSON, nullable=False, default=dict)  # tabs, groups, split, fileBrowserCwd
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=True)

    __table_args__ = (UniqueConstraint("user_id", "path", name="uq_workspace_user_path"),)

    # ── Class methods ────────────────────────────────────────

    @staticmethod
    async def get_by_user(user_id: str) -> list[Workspace]:
        """List all workspaces for a user."""
        async with await get_db() as db:
            result = await db.execute(
                select(Workspace).where(Workspace.user_id == user_id).order_by(Workspace.created_at)
            )
            return list(result.scalars().all())

    @staticmethod
    async def get_by_path(user_id: str, path: str) -> Workspace | None:
        """Get a single workspace by user + path."""
        async with await get_db() as db:
            result = await db.execute(
                select(Workspace).where(
                    Workspace.user_id == user_id,
                    Workspace.path == path,
                )
            )
            return result.scalar_one_or_none()

    @staticmethod
    async def get_by_user_path(user_id: str, path: str) -> Workspace | None:
        """Newest row for a user whose path resolves to `path` (or None)."""
        target = normalize_path(path)
        if not target:
            return None
        matches = [
            workspace
            for workspace in await Workspace.get_by_user(user_id)
            if normalize_path(workspace.path) == target
        ]
        if not matches:
            return None
        return max(matches, key=lambda ws: ws.updated_at or ws.created_at or 0)

    @staticmethod
    def prompt_from(row: Workspace | None) -> str:
        """The workspace's dashboard description, if it has one."""
        data = (row.data if row else None) or {}
        prompt = data.get("prompt") if isinstance(data, dict) else None
        return prompt.strip() if isinstance(prompt, str) else ""

    @staticmethod
    async def get_prompt(user_id: str, path: str) -> str:
        """The short description a workspace injects at the start of its chats."""
        if not user_id or not path:
            return ""
        row = await Workspace.get_by_user_path(user_id, path)
        return Workspace.prompt_from(row)

    @staticmethod
    async def upsert(user_id: str, path: str, name: str, data: dict) -> Workspace:
        """Create or update a workspace."""
        async with await get_db() as db:
            result = await db.execute(
                select(Workspace).where(
                    Workspace.user_id == user_id,
                    Workspace.path == path,
                )
            )
            ws = result.scalar_one_or_none()
            now = int(time.time())
            if ws:
                ws.name = name
                ws.data = data
                ws.updated_at = now
            else:
                ws = Workspace(
                    user_id=user_id,
                    path=path,
                    name=name,
                    data=data,
                    created_at=now,
                    updated_at=now,
                )
                db.add(ws)
            await db.commit()
            return ws

    @staticmethod
    async def delete_by_path(user_id: str, path: str) -> bool:
        """Delete a workspace. Returns True if it existed."""
        async with await get_db() as db:
            result = await db.execute(
                delete(Workspace).where(
                    Workspace.user_id == user_id,
                    Workspace.path == path,
                )
            )
            await db.commit()
            return result.rowcount > 0

    @staticmethod
    async def delete_by_paths(user_id: str, paths: list[str]) -> int:
        """Delete workspaces by path. Returns the number of deleted rows."""
        if not paths:
            return 0
        async with await get_db() as db:
            result = await db.execute(
                delete(Workspace).where(
                    Workspace.user_id == user_id,
                    Workspace.path.in_(paths),
                )
            )
            await db.commit()
            return result.rowcount or 0

    @staticmethod
    async def delete_by_user(user_id: str) -> None:
        """Delete all workspaces for a user."""
        async with await get_db() as db:
            await db.execute(delete(Workspace).where(Workspace.user_id == user_id))
            await db.commit()
