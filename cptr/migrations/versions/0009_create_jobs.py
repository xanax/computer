"""create jobs, backfilled from workspace_todos

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-20

Phase 1 of notes/NOTES-myriad-job-queue.md. One row type for work, viewed
through three axes: `executor` (who runs it), `trigger` (when it starts) and
`resource` (what it may touch at the same time).

Non-destructive: nothing is dropped. Every `workspace_todos` row is copied in
as `executor='human'`, `trigger='manual'` with its id preserved, so links,
API responses and the dashboard keep working while the old table is still
readable for one release.
"""

from alembic import op
import sqlalchemy as sa


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("workspace", sa.Text(), nullable=False),
        # Serialization lane. '' = free-running; see §5 of the design note.
        sa.Column("resource", sa.Text(), nullable=False, server_default=""),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False, server_default="task"),
        sa.Column("executor", sa.Text(), nullable=False),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column("trigger_at", sa.BigInteger(), nullable=True),
        sa.Column("rrule", sa.Text(), nullable=True),
        sa.Column("deadline", sa.BigInteger(), nullable=True),
        sa.Column("payload", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("depends_on", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("parent_chat", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False, server_default="human"),
        sa.Column("origin_chat", sa.Text(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_jobs_user_ws_status", "jobs", ["user_id", "workspace", "status"], unique=False
    )
    op.create_index("ix_jobs_due", "jobs", ["status", "trigger_at"], unique=False)
    op.create_index(
        "ix_jobs_resource", "jobs", ["resource", "status", "priority", "created_at"], unique=False
    )

    # ── Backfill: every existing todo becomes a human job ───────────
    # Ids are preserved. `meta` records where the row came from so a later
    # migration can tell backfilled rows from native ones.
    op.execute(
        sa.text(
            """
            INSERT INTO jobs (
                id, user_id, workspace, resource, title, kind, executor, trigger,
                trigger_at, rrule, deadline, payload, status, priority, depends_on,
                attempts, last_error, parent_chat, source, origin_chat, meta,
                created_at, updated_at
            )
            SELECT
                id, user_id, workspace, '', title, 'task', 'human', 'manual',
                NULL, NULL, NULL, NULL, status, 0, NULL,
                0, NULL, NULL, source, NULL, '{"legacy": "workspace_todos"}',
                created_at, updated_at
            FROM workspace_todos
            """
        )
    )


def downgrade() -> None:
    # The backfill is a copy, so dropping jobs loses only rows created after
    # this migration ran. `workspace_todos` is untouched either way.
    op.drop_index("ix_jobs_resource", table_name="jobs")
    op.drop_index("ix_jobs_due", table_name="jobs")
    op.drop_index("ix_jobs_user_ws_status", table_name="jobs")
    op.drop_table("jobs")
