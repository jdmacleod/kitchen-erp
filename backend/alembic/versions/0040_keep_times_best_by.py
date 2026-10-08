"""Keep times on ingredients and best-by dates on purchase lines (2Q).

An ingredient records how many whole days it keeps, unopened, at room
temperature, in the fridge and in the freezer; null means the storage charts give
no time for that place. A purchase line records where it is stored, its best-by
date, and whether that date was inferred from a keep time, printed on the label,
or set by a person.

Downgrade drops the columns.

Revision ID: 0040
Revises: 0039
Create Date: 2026-10-08
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None

PLACES = ("room", "fridge", "freezer")


def upgrade() -> None:
    for place in PLACES:
        op.add_column("ingredient", sa.Column(f"keep_{place}_days", sa.Integer()))
        op.create_check_constraint(
            f"ck_ingredient_keep_{place}_days", "ingredient", f"keep_{place}_days >= 0"
        )
    op.add_column("purchase_line", sa.Column("stored_in", sa.String(8)))
    op.add_column("purchase_line", sa.Column("best_by", sa.Date()))
    op.add_column("purchase_line", sa.Column("best_by_source", sa.String(8)))
    op.create_check_constraint(
        "ck_purchase_line_stored_in",
        "purchase_line",
        "stored_in IN ('room', 'fridge', 'freezer')",
    )
    op.create_check_constraint(
        "ck_purchase_line_best_by_source",
        "purchase_line",
        "best_by_source IN ('inferred', 'printed', 'person')",
    )
    op.create_check_constraint(
        "ck_purchase_line_best_by",
        "purchase_line",
        "best_by IS NULL OR best_by_source IS NOT NULL",
    )


def downgrade() -> None:
    for name in ("best_by", "best_by_source", "stored_in"):
        op.drop_constraint(f"ck_purchase_line_{name}", "purchase_line", type_="check")
    op.drop_column("purchase_line", "best_by_source")
    op.drop_column("purchase_line", "best_by")
    op.drop_column("purchase_line", "stored_in")
    for place in PLACES:
        op.drop_constraint(f"ck_ingredient_keep_{place}_days", "ingredient", type_="check")
        op.drop_column("ingredient", f"keep_{place}_days")
