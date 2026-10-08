"""Ingredients may be ``frozen``: bought and kept frozen (USDA storage charts).

``ingredient.perishability`` says how fast an ingredient spoils where it is
normally kept. The fourth value joins shelf_stable, refrigerated and fresh, so a
frozen vegetable is no longer filed as shelf-stable.

Downgrade turns ``frozen`` back into ``shelf_stable``, which is what frozen
ingredients were filed as before, then restores the old constraint.

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-08
"""

from __future__ import annotations

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None

OLD = "perishability IN ('shelf_stable', 'refrigerated', 'fresh')"
NEW = "perishability IN ('shelf_stable', 'refrigerated', 'fresh', 'frozen')"


def upgrade() -> None:
    op.drop_constraint("ck_ingredient_perishability", "ingredient", type_="check")
    op.create_check_constraint("ck_ingredient_perishability", "ingredient", NEW)


def downgrade() -> None:
    op.execute(
        "UPDATE ingredient SET perishability = 'shelf_stable' WHERE perishability = 'frozen'"
    )
    op.drop_constraint("ck_ingredient_perishability", "ingredient", type_="check")
    op.create_check_constraint("ck_ingredient_perishability", "ingredient", OLD)
