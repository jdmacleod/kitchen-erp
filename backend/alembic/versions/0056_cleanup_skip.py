"""Catalog cleanup: suggestions a person skipped (#349).

``cleanup_skip`` holds the key of each skipped suggestion. The key carries the
kind, the row and what the suggestion proposes, so a skip lasts until the data
changes and the suggestion becomes another. A decision, not a fact: the runtime
role inserts rows through the default privileges.

Downgrade drops the table; skipped suggestions would be offered again.

Revision ID: 0056
Revises: 0055
Create Date: 2026-10-14
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cleanup_skip",
        sa.Column("key", sa.String(120), primary_key=True),
        sa.Column("skipped_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("app_user.id")),
        sa.Column(
            "skipped_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def downgrade() -> None:
    op.drop_table("cleanup_skip")
