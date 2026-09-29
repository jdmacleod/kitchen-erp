"""``ref_usda_portion.portion_unit`` becomes text.

Survey (FNDDS) portions carry their unit as free text, and the 2026-04-30
FoodData Central download has one longer than the old 100-character limit.
``portion_label`` beside it is already text.

Downgrading truncates any longer unit to 100 characters. The table is
reference data, replaced whole by ``kerp import usda-portions``, so a
re-import after upgrading again restores it.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-29
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "ref_usda_portion", "portion_unit", type_=sa.Text(), existing_type=sa.String(100)
    )


def downgrade() -> None:
    op.alter_column(
        "ref_usda_portion",
        "portion_unit",
        type_=sa.String(100),
        existing_type=sa.Text(),
        postgresql_using="left(portion_unit, 100)",
    )
