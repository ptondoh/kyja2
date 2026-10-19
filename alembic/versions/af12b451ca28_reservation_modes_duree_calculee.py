"""reservation modes duree calculee

Revision ID: af12b451ca28
Revises: fafd38166d81
Create Date: 2026-10-18 22:04:37.471197

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "af12b451ca28"
down_revision: Union[str, Sequence[str], None] = "fafd38166d81"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

pricing_mode = postgresql.ENUM("estimate", "quote", name="pricing_mode", create_type=False)
booking_mode = postgresql.ENUM("catalog", "custom", name="booking_mode", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM("estimate", "quote", name="pricing_mode").create(bind, checkfirst=True)
    postgresql.ENUM("catalog", "custom", name="booking_mode").create(bind, checkfirst=True)

    # Services : mode de tarification, prix optionnel (NULL = sur devis)
    op.add_column(
        "services",
        sa.Column("pricing_mode", pricing_mode, nullable=False, server_default="estimate"),
    )
    op.alter_column("services", "price", existing_type=sa.NUMERIC(10, 2), nullable=True)

    # Coiffeuses : coefficient de durée (1.00 = standard)
    op.add_column(
        "hairdressers",
        sa.Column("duration_coefficient", sa.Numeric(4, 2), nullable=False, server_default="1.00"),
    )
    op.create_check_constraint(
        "hairdresser_coefficient", "hairdressers", "duration_coefficient BETWEEN 0.50 AND 3.00"
    )

    # Rendez-vous : le prix devient « estimé » ; calcul mémorisé ; prix final saisi au salon
    op.alter_column("appointments", "price", new_column_name="estimated_price", nullable=True)
    op.add_column(
        "appointments",
        sa.Column("booking_mode", booking_mode, nullable=False, server_default="catalog"),
    )
    op.add_column("appointments", sa.Column("client_notes", sa.Text(), nullable=True))
    op.add_column(
        "appointments", sa.Column("base_duration_min", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "appointments",
        sa.Column("coefficient_used", sa.Numeric(4, 2), nullable=False, server_default="1.00"),
    )
    op.add_column(
        "appointments", sa.Column("buffer_min", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "appointments",
        sa.Column("computed_duration_min", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("appointments", sa.Column("final_price", sa.Numeric(10, 2), nullable=True))
    # Les valeurs par défaut ne servent qu'aux lignes existantes : le code fournit toujours les valeurs.
    for table, col in (
        ("services", "pricing_mode"),
        ("hairdressers", "duration_coefficient"),
        ("appointments", "booking_mode"),
        ("appointments", "base_duration_min"),
        ("appointments", "coefficient_used"),
        ("appointments", "buffer_min"),
        ("appointments", "computed_duration_min"),
    ):
        op.alter_column(table, col, server_default=None)


def downgrade() -> None:
    for col in (
        "final_price",
        "computed_duration_min",
        "buffer_min",
        "coefficient_used",
        "base_duration_min",
        "client_notes",
        "booking_mode",
    ):
        op.drop_column("appointments", col)
    op.alter_column("appointments", "estimated_price", new_column_name="price", nullable=False)
    op.drop_constraint("hairdresser_coefficient", "hairdressers", type_="check")
    op.drop_column("hairdressers", "duration_coefficient")
    op.execute("UPDATE services SET price = 0 WHERE price IS NULL")
    op.alter_column("services", "price", existing_type=sa.NUMERIC(10, 2), nullable=False)
    op.drop_column("services", "pricing_mode")
    postgresql.ENUM(name="booking_mode").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="pricing_mode").drop(op.get_bind(), checkfirst=True)
