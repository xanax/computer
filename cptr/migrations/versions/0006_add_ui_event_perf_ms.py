"""add perf_ms to ui_events

The original schema stored a single client timestamp in ``ts``, which the
collector filled with ``performance.now()`` — page-relative, so it reset on
every reload and was useless for ordering events across sessions. ``ts`` now
carries a wall-clock epoch-ms value and ``perf_ms`` holds the monotonic
page-relative reading alongside it, so both views are available.

Nullable so existing rows (which predate the split) stay valid.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-14
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ui_events", sa.Column("perf_ms", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("ui_events", "perf_ms")
