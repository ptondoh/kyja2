"""reservation sans connexion prealable

Revision ID: c5d8a2f01e77
Revises: b3c91e7d4a10
Create Date: 2026-10-19 09:30:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c5d8a2f01e77"
down_revision: Union[str, Sequence[str], None] = "b3c91e7d4a10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "booking_drafts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.alter_column("booking_photos", "client_id", existing_type=sa.Integer(), nullable=True)
    op.add_column("booking_photos", sa.Column("draft_token_hash", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_booking_photos_draft_token_hash"), "booking_photos", ["draft_token_hash"], unique=False)
    op.create_check_constraint(
        "booking_photo_owner", "booking_photos", "client_id IS NOT NULL OR draft_token_hash IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DELETE FROM booking_photos WHERE client_id IS NULL")
    op.drop_constraint("booking_photo_owner", "booking_photos", type_="check")
    op.drop_index(op.f("ix_booking_photos_draft_token_hash"), table_name="booking_photos")
    op.drop_column("booking_photos", "draft_token_hash")
    op.alter_column("booking_photos", "client_id", existing_type=sa.Integer(), nullable=False)
    op.drop_table("booking_drafts")
