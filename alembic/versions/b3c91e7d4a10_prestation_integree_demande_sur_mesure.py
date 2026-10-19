"""prestation integree demande sur mesure

Revision ID: b3c91e7d4a10
Revises: 7c18b1ad1dca
Create Date: 2026-10-18 23:10:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b3c91e7d4a10"
down_revision: Union[str, Sequence[str], None] = "7c18b1ad1dca"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("services", sa.Column("is_custom_request", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index(
        "uq_services_custom_request", "services", ["is_custom_request"], unique=True,
        postgresql_where=sa.text("is_custom_request"),
    )
    # La demande sur mesure existe dès l'installation : durée de base et repos modifiables par l'admin.
    op.execute(
        """
        INSERT INTO services (name, description, base_duration_min, buffer_min, pricing_mode, price, is_active, is_custom_request)
        VALUES ('Demande sur mesure', 'Coiffure décrite par la cliente. Prix confirmé au salon.', 120, 15, 'quote', NULL, true, true)
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM services WHERE is_custom_request AND id NOT IN (SELECT service_id FROM appointments)")
    op.drop_index("uq_services_custom_request", table_name="services")
    op.drop_column("services", "is_custom_request")
