"""Barcode company prefixes of store-brand families (spec 16, 2R-3).

``gs1_prefix`` holds the start of the 13-digit barcode a family's own brands are
printed under, and ``gs1_prefix_family`` which families each belongs to (a
prefix two families share has two rows). The dataset owns them: ``kerp import
brands`` replaces them all. A prefix is evidence for a family, never against one.

Downgrade drops both tables.

Revision ID: 0047
Revises: 0046
Create Date: 2026-10-10
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None

_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "gs1_prefix",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("prefix", sa.String(12), nullable=False),
        sa.UniqueConstraint("prefix", name="uq_gs1_prefix"),
        sa.CheckConstraint("prefix ~ '^[0-9]{6,12}$'", name="ck_gs1_prefix_digits"),
    )
    op.create_table(
        "gs1_prefix_family",
        sa.Column(
            "prefix_id", _UUID, sa.ForeignKey("gs1_prefix.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "family_id",
            _UUID,
            sa.ForeignKey("brand_family.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("gs1_prefix_family")
    op.drop_table("gs1_prefix")
