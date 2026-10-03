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


#: Who may author a note: the human at the dashboard, or the agent (a chat).
NOTE_AUTHORS = ("human", "agent")
#: Caps on the stored list: `data` is one JSON column, and every note is read
#: back into the system prompt, so it may not grow without bound.
MAX_NOTES = 200
MAX_NOTE_CHARS = 4000


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

    # ── Notes ────────────────────────────────────────────────
    #
    # Short notes stuck on a workspace: "the deploy script moved", "tests/x is
    # flaky". Both sides write them — the human from the workspace dashboard, the
    # agent through the `add_workspace_note` tool — and both sides read them: the
    # dashboard lists them and every chat in the workspace opens with them, so a
    # note is how one session leaves a fact for the next. They live in
    # `workspaces.data["notes"]` (no schema change) as a list of
    # `{"id", "text", "author", "created_at"}`, oldest first.

    @staticmethod
    def notes_from(row: Workspace | None) -> list[dict]:
        """The workspace's notes, normalised and in insertion order."""
        data = (row.data if row else None) or {}
        raw = data.get("notes") if isinstance(data, dict) else None
        if not isinstance(raw, list):
            return []
        notes: list[dict] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            text = entry.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            author = entry.get("author")
            notes.append(
                {
                    "id": str(entry.get("id") or ""),
                    "text": text.strip(),
                    "author": author if author in NOTE_AUTHORS else "human",
                    "created_at": int(entry.get("created_at") or 0),
                }
            )
        return notes

    @staticmethod
    async def get_notes(user_id: str, path: str) -> list[dict]:
        """The notes a workspace carries ([] when it has none or is unknown)."""
        if not user_id or not path:
            return []
        row = await Workspace.get_by_user_path(user_id, path)
        return Workspace.notes_from(row)

    @staticmethod
    def make_note(text: str, author: str = "human") -> dict:
        """A single note as it is stored. Trims; raises ValueError on blank text."""
        text = (text or "").strip()
        if not text:
            raise ValueError("note text is required")
        if len(text) > MAX_NOTE_CHARS:
            raise ValueError(f"note is too long (max {MAX_NOTE_CHARS} characters)")
        return {
            "id": uuid.uuid4().hex[:12],
            "text": text,
            "author": author if author in NOTE_AUTHORS else "human",
            "created_at": int(time.time() * 1000),
        }

    @staticmethod
    async def add_note(user_id: str, path: str, text: str, author: str = "human") -> dict:
        """Append a note to a workspace and return the stored note.

        Reads the row, appends and writes it back whole, because `data` is one
        JSON column: a partial write would drop the layout beside it.
        """
        note = Workspace.make_note(text, author)
        row = await Workspace.get_by_user_path(user_id, path)
        notes = Workspace.notes_from(row)
        if len(notes) >= MAX_NOTES:
            raise ValueError(f"this workspace already has {MAX_NOTES} notes — remove one first")
        data = dict(row.data or {}) if row else {}
        data["notes"] = [*notes, note]
        name = row.name if row and row.name else os.path.basename(normalize_path(path)) or path
        await Workspace.upsert(user_id, path, name, data)
        return note

    @staticmethod
    async def delete_note(user_id: str, path: str, note_id: str) -> bool:
        """Remove one note by id. False when the note was not there."""
        row = await Workspace.get_by_user_path(user_id, path)
        if not row:
            return False
        notes = Workspace.notes_from(row)
        remaining = [note for note in notes if note.get("id") != note_id]
        if len(remaining) == len(notes):
            return False
        data = dict(row.data or {})
        if remaining:
            data["notes"] = remaining
        else:
            data.pop("notes", None)
        await Workspace.upsert(
            user_id, path, row.name or os.path.basename(normalize_path(path)) or path, data
        )
        return True

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
