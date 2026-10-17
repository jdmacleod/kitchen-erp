"""USDA refuse per FoodData Central food, for the ingredient form's yield (issue 282).

``fdc_food.refuse_pct`` is the inedible share of a food as bought (bone, peel,
seeds, ...), as a percentage: the sum of ``pct_weight`` over the food's
``food_component.csv`` rows marked as refuse. Null where USDA gives no refuse
figure. Reference data, replaced on every ``kerp import usda``; the existing
grants on ``fdc_food`` cover the new column.

Downgrade drops the column; the form stops offering a refuse figure.

Revision ID: 0061
Revises: 0060
Create Date: 2026-10-16
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("fdc_food", sa.Column("refuse_pct", sa.Numeric(6, 3)))
    op.create_check_constraint(
        "ck_fdc_food_refuse_pct",
        "fdc_food",
        "refuse_pct IS NULL OR (refuse_pct >= 0 AND refuse_pct < 100)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_fdc_food_refuse_pct", "fdc_food", type_="check")
    op.drop_column("fdc_food", "refuse_pct")
