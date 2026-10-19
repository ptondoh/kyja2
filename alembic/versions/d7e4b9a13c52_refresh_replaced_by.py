"""lien de succession des jetons de renouvellement

Revision ID: d7e4b9a13c52
Revises: c5d8a2f01e77
Create Date: 2026-10-19 11:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d7e4b9a13c52"
down_revision: Union[str, Sequence[str], None] = "c5d8a2f01e77"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("refresh_tokens", sa.Column("replaced_by_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("refresh_tokens", "replaced_by_id")
