"""Possible duplicate ingredients: "Not the same" remembered for a pair (#346).

``ingredient_distinct_pair`` holds a person's decision that two ingredients the
duplicate finder pairs are in fact different, so the pair is never offered
again. It mirrors ``product_distinct_pair`` (0038): a decision, not a fact; the
runtime role may insert and delete rows through the default privileges, and
the pair is stored in id order so either order of the same two finds it.

Downgrade drops the table; the pairs would then be offered again.

Revision ID: 0055
Revises: 0054
Create Date: 2026-10-13
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ingredient_distinct_pair",
        sa.Column(
            "ingredient_a",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ingredient.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "ingredient_b",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ingredient.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("app_user.id")),
        sa.Column(
            "decided_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("ingredient_a < ingredient_b", name="ck_ingredient_distinct_pair_order"),
    )
    op.create_index("ix_ingredient_distinct_pair_b", "ingredient_distinct_pair", ["ingredient_b"])


def downgrade() -> None:
    op.drop_index("ix_ingredient_distinct_pair_b", table_name="ingredient_distinct_pair")
    op.drop_table("ingredient_distinct_pair")
