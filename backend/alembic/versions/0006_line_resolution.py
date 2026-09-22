"""Purchase lines carry their normalized text and current suggestions.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-21
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("purchase_line", sa.Column("raw_text_norm", sa.Text()))
    op.add_column(
        "purchase_line",
        sa.Column("suggestions", postgresql.JSONB(), nullable=False, server_default="[]"),
    )
    op.create_index("ix_purchase_line_raw_text_norm", "purchase_line", ["raw_text_norm"])
    op.create_index("ix_purchase_line_resolution", "purchase_line", ["resolution", "line_kind"])


def downgrade() -> None:
    op.drop_index("ix_purchase_line_resolution", table_name="purchase_line")
    op.drop_index("ix_purchase_line_raw_text_norm", table_name="purchase_line")
    op.drop_column("purchase_line", "suggestions")
    op.drop_column("purchase_line", "raw_text_norm")
