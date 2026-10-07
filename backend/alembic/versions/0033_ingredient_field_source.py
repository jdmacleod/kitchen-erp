"""Ingredient files: where each ingredient field came from, and imported spellings (#241).

``ingredient.field_source`` records, per field, what a source last wrote there,
as vendors and products already do, so an import never overwrites a field a
person has edited since (the 1F edit-wins rule, now in services.interchange).
Ingredients that exist before this have none recorded, so every value they hold
counts as the household's own.

``ingredient_alias.source`` gains ``import`` for spellings an ingredient file
adds.

Downgrade drops the column and turns imported spellings into ``manual`` ones
before restoring the old constraint, so no spelling is lost.

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-07
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None

OLD_SOURCES = "'standard', 'generated', 'rename', 'merge', 'manual'"
NEW_SOURCES = OLD_SOURCES + ", 'import'"


def upgrade() -> None:
    op.add_column(
        "ingredient",
        sa.Column(
            "field_source",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.drop_constraint("ck_ingredient_alias_source", "ingredient_alias", type_="check")
    op.create_check_constraint(
        "ck_ingredient_alias_source", "ingredient_alias", f"source IN ({NEW_SOURCES})"
    )


def downgrade() -> None:
    op.execute("UPDATE ingredient_alias SET source = 'manual' WHERE source = 'import'")
    op.drop_constraint("ck_ingredient_alias_source", "ingredient_alias", type_="check")
    op.create_check_constraint(
        "ck_ingredient_alias_source", "ingredient_alias", f"source IN ({OLD_SOURCES})"
    )
    op.drop_column("ingredient", "field_source")
