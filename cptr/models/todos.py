"""WorkspaceTodo and TodoRequest models with data-access class methods."""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, Column, Index, Text, delete, select, update

from cptr.models.base import Base
from cptr.utils.db import get_db


def _uuid() -> str:
    return str(uuid.uuid4())


class WorkspaceTodo(Base):
    """A single todo item scoped to a (user, workspace) pair."""

    __tablename__ = "workspace_todos"

    id = Column(Text, primary_key=True, default=_uuid)
    user_id = Column(Text, nullable=False)
    workspace = Column(Text, nullable=False)
    title = Column(Text, nullable=False)
    status = Column(Text, nullable=False)  # "open" | "done"
    source = Column(Text, nullable=False)  # "human" | "chat"
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (Index("ix_workspace_todos_user_ws", "user_id", "workspace"),)

    # ── Class methods ────────────────────────────────────────

    @staticmethod
    async def get_by_id(todo_id: str) -> WorkspaceTodo | None:
        async with await get_db() as db:
            result = await db.execute(
                select(WorkspaceTodo).where(WorkspaceTodo.id == todo_id)
            )
            return result.scalar_one_or_none()

    @staticmethod
    async def list_for_workspace(user_id: str, workspace: str) -> list[WorkspaceTodo]:
        async with await get_db() as db:
            result = await db.execute(
                select(WorkspaceTodo)
                .where(
                    WorkspaceTodo.user_id == user_id,
                    WorkspaceTodo.workspace == workspace,
                )
                .order_by(WorkspaceTodo.created_at.asc())
            )
            return list(result.scalars().all())

    @staticmethod
    async def create(
        user_id: str,
        workspace: str,
        title: str,
        source: str,
        created_at: int,
        status: str = "open",
    ) -> WorkspaceTodo:
        async with await get_db() as db:
            todo = WorkspaceTodo(
                user_id=user_id,
                workspace=workspace,
                title=title,
                status=status,
                source=source,
                created_at=created_at,
                updated_at=created_at,
            )
            db.add(todo)
            await db.commit()
            await db.refresh(todo)
            return todo

    @staticmethod
    async def update_status(todo_id: str, status: str, updated_at: int) -> bool:
        async with await get_db() as db:
            result = await db.execute(
                update(WorkspaceTodo)
                .where(WorkspaceTodo.id == todo_id)
                .values(status=status, updated_at=updated_at)
            )
            await db.commit()
            return result.rowcount > 0

    @staticmethod
    async def delete(todo_id: str) -> bool:
        async with await get_db() as db:
            result = await db.execute(delete(WorkspaceTodo).where(WorkspaceTodo.id == todo_id))
            await db.commit()
            return result.rowcount > 0


class TodoRequest(Base):
    """A chat-proposed todo mutation awaiting human verification."""

    __tablename__ = "todo_requests"

    id = Column(Text, primary_key=True, default=_uuid)
    user_id = Column(Text, nullable=False)
    workspace = Column(Text, nullable=False)
    action = Column(Text, nullable=False)  # "add" | "complete" | "remove"
    todo_id = Column(Text, nullable=True)  # target todo for complete/remove
    title = Column(Text, nullable=True)  # new title for add
    status = Column(Text, nullable=False)  # "pending" | "approved" | "rejected"
    created_at = Column(BigInteger, nullable=False)
    resolved_at = Column(BigInteger, nullable=True)

    __table_args__ = (Index("ix_todo_requests_user_ws", "user_id", "workspace"),)

    # ── Class methods ────────────────────────────────────────

    @staticmethod
    async def get_by_id(request_id: str) -> TodoRequest | None:
        async with await get_db() as db:
            result = await db.execute(
                select(TodoRequest).where(TodoRequest.id == request_id)
            )
            return result.scalar_one_or_none()

    @staticmethod
    async def list_pending(user_id: str, workspace: str) -> list[TodoRequest]:
        async with await get_db() as db:
            result = await db.execute(
                select(TodoRequest)
                .where(
                    TodoRequest.user_id == user_id,
                    TodoRequest.workspace == workspace,
                    TodoRequest.status == "pending",
                )
                .order_by(TodoRequest.created_at.asc())
            )
            return list(result.scalars().all())

    @staticmethod
    async def create(
        user_id: str,
        workspace: str,
        action: str,
        created_at: int,
        todo_id: str | None = None,
        title: str | None = None,
    ) -> TodoRequest:
        async with await get_db() as db:
            req = TodoRequest(
                user_id=user_id,
                workspace=workspace,
                action=action,
                todo_id=todo_id,
                title=title,
                status="pending",
                created_at=created_at,
            )
            db.add(req)
            await db.commit()
            await db.refresh(req)
            return req

    @staticmethod
    async def resolve(request_id: str, status: str, resolved_at: int) -> bool:
        """Resolve a pending request. Returns False if not found or already resolved."""
        async with await get_db() as db:
            result = await db.execute(
                update(TodoRequest)
                .where(
                    TodoRequest.id == request_id,
                    TodoRequest.status == "pending",
                )
                .values(status=status, resolved_at=resolved_at)
            )
            await db.commit()
            return result.rowcount > 0
