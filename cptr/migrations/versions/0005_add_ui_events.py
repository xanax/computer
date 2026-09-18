"""add ui_events table for client UI performance metrics

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-14
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ui_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), nullable=True),
        sa.Column("workspace", sa.Text(), nullable=True),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("ts", sa.Float(), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_ui_events_user_id", "ui_events", ["user_id"])
    op.create_index("ix_ui_events_session_id", "ui_events", ["session_id"])
    op.create_index("ix_ui_event_kind_created", "ui_events", ["kind", "created_at"])
    op.create_index("ix_ui_event_label_created", "ui_events", ["label", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_ui_event_label_created", table_name="ui_events")
    op.drop_index("ix_ui_event_kind_created", table_name="ui_events")
    op.drop_index("ix_ui_events_session_id", table_name="ui_events")
    op.drop_index("ix_ui_events_user_id", table_name="ui_events")
    op.drop_table("ui_events")
