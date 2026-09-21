"""Units.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-21
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "unit",
        sa.Column("code", sa.String(16), primary_key=True),
        sa.Column("dimension", sa.String(8), nullable=False),
        sa.Column("to_base_factor", sa.Numeric(), nullable=False),
        sa.Column("system", sa.String(8), nullable=False),
        sa.Column("aliases", postgresql.ARRAY(sa.String()), nullable=False),
        sa.CheckConstraint("dimension IN ('mass', 'volume', 'count')", name="ck_unit_dimension"),
        sa.CheckConstraint("system IN ('us', 'metric', 'any')", name="ck_unit_system"),
    )


def downgrade() -> None:
    op.drop_table("unit")
