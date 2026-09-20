"""add workspace todos

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-18
"""

from alembic import op
import sqlalchemy as sa


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_todos",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("workspace", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workspace_todos_user_ws", "workspace_todos", ["user_id", "workspace"], unique=False
    )

    op.create_table(
        "todo_requests",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("workspace", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("todo_id", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("resolved_at", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_todo_requests_user_ws", "todo_requests", ["user_id", "workspace"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_todo_requests_user_ws", table_name="todo_requests")
    op.drop_table("todo_requests")
    op.drop_index("ix_workspace_todos_user_ws", table_name="workspace_todos")
    op.drop_table("workspace_todos")
