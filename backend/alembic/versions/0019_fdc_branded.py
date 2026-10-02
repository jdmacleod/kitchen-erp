"""USDA branded foods, opt-in (04, 2L): ``fdc_branded``.

One row per GTIN-14 from a local FoodData Central branded download, loaded by
``kerp import usda --branded``. A barcode the catalog does not know is looked up
here before it becomes a proposal. It is a local read; nothing is sent anywhere.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-01
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fdc_branded",
        sa.Column("gtin", sa.String(14), primary_key=True),
        sa.Column("fdc_id", sa.Integer(), nullable=False),
        sa.Column("brand", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("package_size", sa.Text(), nullable=True),
        sa.Column("release_date", sa.Date(), nullable=True),
        sa.CheckConstraint("gtin ~ '^[0-9]{14}$'", name="ck_fdc_branded_gtin"),
    )


def downgrade() -> None:
    op.drop_table("fdc_branded")
