"""Ingredients may be ``shelf_months``: kept at room temperature, good for months.

The USDA storage charts file nuts, chips, crackers, oils and whole-grain flour
under shelf-stable foods, but give them weeks to months (chips 2 months, crackers
8, jarred nuts 12, whole-wheat flour 1) where canned goods, rice, pasta and sugar
keep years. The fifth value separates the two; ``shelf_stable`` now means a year
or more.

Downgrade turns ``shelf_months`` back into ``shelf_stable``, then restores the
old constraint.

Revision ID: 0039
Revises: 0038
Create Date: 2026-10-08
"""

from __future__ import annotations

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None

OLD = "perishability IN ('shelf_stable', 'refrigerated', 'fresh', 'frozen')"
NEW = "perishability IN ('shelf_stable', 'shelf_months', 'refrigerated', 'fresh', 'frozen')"


def upgrade() -> None:
    op.drop_constraint("ck_ingredient_perishability", "ingredient", type_="check")
    op.create_check_constraint("ck_ingredient_perishability", "ingredient", NEW)


def downgrade() -> None:
    op.execute(
        "UPDATE ingredient SET perishability = 'shelf_stable' WHERE perishability = 'shelf_months'"
    )
    op.drop_constraint("ck_ingredient_perishability", "ingredient", type_="check")
    op.create_check_constraint("ck_ingredient_perishability", "ingredient", OLD)
