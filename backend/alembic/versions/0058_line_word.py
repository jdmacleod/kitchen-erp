"""Learned expansions of the words vendors print on receipt lines (#388).

``line_word`` pairs a printed word ("ygrt") with the word it stands for
("yogurt"), at one vendor or, with a null ``vendor_id``, at any vendor. Rows are
learned when a person confirms a line's product, and backfilled from existing
aliases by ``kerp naming learn-words``. Derived suggestions, not facts: the
runtime role writes them through the default privileges.

Downgrade drops the table; the expansions can be learned again from the aliases.

Revision ID: 0058
Revises: 0057
Create Date: 2026-10-16
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "line_word",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "vendor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vendor.id", ondelete="CASCADE"),
        ),
        sa.Column("word", sa.Text(), nullable=False),
        sa.Column("expansion", sa.Text(), nullable=False),
        sa.Column("confirmed_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("source", sa.String(16), nullable=False, server_default="learned"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("source IN ('learned', 'person')", name="ck_line_word_source"),
        sa.CheckConstraint("confirmed_count >= 0", name="ck_line_word_count"),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_line_word_key ON line_word "
        "(vendor_id, word, expansion) NULLS NOT DISTINCT"
    )


def downgrade() -> None:
    op.drop_table("line_word")
