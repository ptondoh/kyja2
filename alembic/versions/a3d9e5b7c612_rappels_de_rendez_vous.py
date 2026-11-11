"""rappels de rendez-vous (la veille et 2 h avant)

Revision ID: a3d9e5b7c612
Revises: f2c7d4a91b35
Create Date: 2026-11-10 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3d9e5b7c612"
down_revision: Union[str, None] = "f2c7d4a91b35"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("appointments", sa.Column("reminder_day_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("appointments", sa.Column("reminder_hours_sent_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("appointments", "reminder_hours_sent_at")
    op.drop_column("appointments", "reminder_day_sent_at")
