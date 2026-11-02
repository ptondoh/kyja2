"""prix final des demandes sur mesure, notifications et journal des messages

Revision ID: f2c7d4a91b35
Revises: e8a1c5d6f731
Create Date: 2026-11-03 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2c7d4a91b35"
down_revision: Union[str, Sequence[str], None] = "e8a1c5d6f731"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nouveaux statuts : une demande sur mesure attend son prix, puis la réponse de la cliente.
    # (PostgreSQL exige de valider l'ajout d'une valeur d'énumération avant de s'en servir.)
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE appointment_status ADD VALUE IF NOT EXISTS 'pending_quote'")
        op.execute("ALTER TYPE appointment_status ADD VALUE IF NOT EXISTS 'awaiting_client'")

    op.add_column("appointments", sa.Column("quoted_price", sa.Numeric(10, 2), nullable=True))
    op.add_column("appointments", sa.Column("quote_proposed_by_id", sa.Integer(), nullable=True))
    op.add_column("appointments", sa.Column("quote_proposed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("appointments", sa.Column("quote_decided_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("appointments", sa.Column("quote_token_hash", sa.String(length=64), nullable=True))
    op.add_column("appointments", sa.Column("quote_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("appointments", sa.Column("cancel_reason", sa.String(length=40), nullable=True))
    op.create_foreign_key("fk_appointments_quote_proposed_by", "appointments", "users", ["quote_proposed_by_id"], ["id"], ondelete="SET NULL")
    op.create_index(op.f("ix_appointments_quote_token_hash"), "appointments", ["quote_token_hash"], unique=True)

    # Préférences de notification : la cliente choisit comment être avertie.
    op.add_column("users", sa.Column("pref_in_app", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("users", sa.Column("pref_browser_alert", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("users", sa.Column("pref_email", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("users", sa.Column("pref_sms", sa.Boolean(), nullable=False, server_default=sa.false()))

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.String(length=1000), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=True),
        sa.Column("appointment_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["appointment_id"], ["appointments.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notifications_user_read", "notifications", ["user_id", "read_at"], unique=False)

    op.create_table(
        "message_outbox",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("channel", sa.String(length=10), nullable=False),
        sa.Column("to_address", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False, server_default="queued"),
        sa.Column("provider", sa.String(length=20), nullable=True),
        sa.Column("provider_ref", sa.String(length=100), nullable=True),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("appointment_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["appointment_id"], ["appointments.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_message_outbox_status"), "message_outbox", ["status"], unique=False)
    op.create_index(op.f("ix_message_outbox_user_id"), "message_outbox", ["user_id"], unique=False)

    for table, col in (("users", "pref_in_app"), ("users", "pref_browser_alert"), ("users", "pref_email"), ("users", "pref_sms")):
        op.alter_column(table, col, server_default=None)
    op.alter_column("message_outbox", "status", server_default=None)
    op.alter_column("message_outbox", "attempts", server_default=None)


def downgrade() -> None:
    op.drop_table("message_outbox")
    op.drop_index("ix_notifications_user_read", table_name="notifications")
    op.drop_table("notifications")
    for col in ("pref_sms", "pref_email", "pref_browser_alert", "pref_in_app"):
        op.drop_column("users", col)
    op.drop_index(op.f("ix_appointments_quote_token_hash"), table_name="appointments")
    op.drop_constraint("fk_appointments_quote_proposed_by", "appointments", type_="foreignkey")
    for col in ("cancel_reason", "quote_expires_at", "quote_token_hash", "quote_decided_at", "quote_proposed_at", "quote_proposed_by_id", "quoted_price"):
        op.drop_column("appointments", col)
    # PostgreSQL ne sait pas retirer une valeur d'énumération : on remet les rendez-vous dans des statuts d'avant.
    op.execute("UPDATE appointments SET status = 'confirmed' WHERE status IN ('pending_quote', 'awaiting_client')")
