"""Model suggestions for naming new products in bulk (04, 2I; #88): ``naming_suggestion``.

One row per vendor and receipt wording, asked for from the naming pass and filled
by the worker: a product name and an ingredient, which is an existing ingredient
or a standard-list key, never free text. Suggestions only pre-fill fields a
person confirms, so the table is working state, not a record of facts.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-01
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "naming_suggestion",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "vendor_id",
            UUID(as_uuid=True),
            sa.ForeignKey("vendor.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("raw_text_norm", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column(
            "ingredient_id",
            UUID(as_uuid=True),
            sa.ForeignKey("ingredient.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("standard_key", sa.String(120), nullable=True),
        sa.Column("error", sa.String(64), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'done', 'failed')", name="ck_naming_suggestion_status"
        ),
        sa.CheckConstraint(
            "ingredient_id IS NULL OR standard_key IS NULL",
            name="ck_naming_suggestion_one_ingredient",
        ),
        sa.UniqueConstraint("vendor_id", "raw_text_norm", name="uq_naming_suggestion_vendor_text"),
    )
    op.create_index(
        "ix_naming_suggestion_pending",
        "naming_suggestion",
        ["created_at"],
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("ix_naming_suggestion_pending", table_name="naming_suggestion")
    op.drop_table("naming_suggestion")
