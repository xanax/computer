"""add chat closed at

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-18
"""

from alembic import op
import sqlalchemy as sa


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chats", sa.Column("closed_at", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("chats", "closed_at")
