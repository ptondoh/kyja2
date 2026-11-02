"""categories gerees par l'admin, archivage des prestations et des coiffeuses

Revision ID: e8a1c5d6f731
Revises: d7e4b9a13c52
Create Date: 2026-11-02 14:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e8a1c5d6f731"
down_revision: Union[str, Sequence[str], None] = "d7e4b9a13c52"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    # Une catégorie ne peut exister qu'une fois, sans tenir compte des majuscules (« Coiffure » = « coiffure »).
    op.create_index("uq_categories_name_ci", "categories", [sa.text("lower(name)")], unique=True)

    op.add_column("services", sa.Column("category_id", sa.Integer(), nullable=True))
    # Les catégories saisies jusqu'ici en texte libre deviennent de vraies catégories (doublons de casse fusionnés).
    op.execute(
        """
        INSERT INTO categories (name)
        SELECT DISTINCT ON (lower(trim(category))) trim(category)
        FROM services
        WHERE category IS NOT NULL AND trim(category) <> ''
        ORDER BY lower(trim(category)), trim(category)
        """
    )
    op.execute(
        """
        UPDATE services s SET category_id = c.id
        FROM categories c
        WHERE s.category IS NOT NULL AND lower(trim(s.category)) = lower(c.name)
        """
    )
    # Supprimer une catégorie ne supprime pas les lignes en base : le code décide quoi faire de ses prestations.
    op.create_foreign_key("fk_services_category", "services", "categories", ["category_id"], ["id"], ondelete="SET NULL")
    op.create_index(op.f("ix_services_category_id"), "services", ["category_id"], unique=False)
    op.drop_index("ix_services_category", table_name="services")
    op.drop_column("services", "category")

    # Archivage : « supprimer » une prestation ou une coiffeuse qui a un historique la cache sans rien perdre.
    op.add_column("services", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("hairdressers", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("hairdressers", "archived_at")
    op.drop_column("services", "archived_at")
    op.add_column("services", sa.Column("category", sa.String(length=100), nullable=True))
    op.execute("UPDATE services s SET category = c.name FROM categories c WHERE s.category_id = c.id")
    op.create_index("ix_services_category", "services", ["category"], unique=False)
    op.drop_index(op.f("ix_services_category_id"), table_name="services")
    op.drop_constraint("fk_services_category", "services", type_="foreignkey")
    op.drop_column("services", "category_id")
    op.drop_index("uq_categories_name_ci", table_name="categories")
    op.drop_table("categories")
