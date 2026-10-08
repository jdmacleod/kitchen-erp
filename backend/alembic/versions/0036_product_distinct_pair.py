"""Possible duplicates: "Not the same" remembered for a pair of products (2P, 03).

``product_distinct_pair`` holds a person's decision that two products the
sameness rules call the same are in fact different, so the pair is never
offered again. It is a decision, not a fact: the runtime role may insert and
delete rows, through the default privileges. The pair is stored in id order,
so either order of the same two products finds it.

Downgrade drops the table; the pairs would then be offered again.

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-08
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_distinct_pair",
        sa.Column(
            "product_a",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "product_b",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("product.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("app_user.id")),
        sa.Column(
            "decided_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("product_a < product_b", name="ck_product_distinct_pair_order"),
    )
    op.create_index("ix_product_distinct_pair_b", "product_distinct_pair", ["product_b"])


def downgrade() -> None:
    op.drop_index("ix_product_distinct_pair_b", table_name="product_distinct_pair")
    op.drop_table("product_distinct_pair")
