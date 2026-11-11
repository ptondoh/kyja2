"""catégories activables, campagnes de promotion et lien des messages avec une campagne

Revision ID: b5e1c7a93d24
Revises: a3d9e5b7c612
Create Date: 2026-11-11 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b5e1c7a93d24"
down_revision: Union[str, None] = "a3d9e5b7c612"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("categories", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.alter_column("categories", "is_active", server_default=None)
    op.create_table(
        "marketing_campaigns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("sms_text", sa.String(length=320), nullable=True),
        sa.Column("send_email", sa.Boolean(), nullable=False),
        sa.Column("send_sms", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False, server_default="draft"),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("recipient_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_marketing_campaigns_status", "marketing_campaigns", ["status"])
    op.add_column("message_outbox", sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("marketing_campaigns.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_message_outbox_campaign_id", "message_outbox", ["campaign_id"])


def downgrade() -> None:
    op.drop_index("ix_message_outbox_campaign_id", table_name="message_outbox")
    op.drop_column("message_outbox", "campaign_id")
    op.drop_index("ix_marketing_campaigns_status", table_name="marketing_campaigns")
    op.drop_table("marketing_campaigns")
    op.drop_column("categories", "is_active")
