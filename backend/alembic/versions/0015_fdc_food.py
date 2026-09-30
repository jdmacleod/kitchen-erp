"""USDA foods and their release (1G): ``fdc_food`` and ``fdc_release``.

``kerp import usda`` loads ``fdc_food`` beside the portions of 1C, with a
usage count: how many FNDDS survey foods use each food as an input. USDA
suggestions rank by it. ``fdc_release`` records each import and the release
date it names. Both are reference data, replaced whole by every import.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-30
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fdc_food",
        sa.Column("fdc_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("data_type", sa.String(40), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("fndds_uses", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_fdc_food_description_trgm",
        "fdc_food",
        [sa.text("lower(description) gin_trgm_ops")],
        postgresql_using="gin",
    )
    op.create_table(
        "fdc_release",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("release_date", sa.Date(), nullable=True),
        sa.Column("source_name", sa.Text(), nullable=False),
        sa.Column("foods", sa.Integer(), nullable=False),
        sa.Column("portions", sa.Integer(), nullable=False),
        sa.Column(
            "imported_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("fdc_release")
    op.drop_index("ix_fdc_food_description_trgm", table_name="fdc_food")
    op.drop_table("fdc_food")
