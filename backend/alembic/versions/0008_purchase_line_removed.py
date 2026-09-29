"""A recorded purchase line can be taken off its purchase without being deleted (#72).

Observations are append-only and keep their line as provenance, so a line that
reached the price book is marked removed instead: every reader of a purchase's
lines skips it, and backup keeps it.

Downgrading drops the marks, so removed lines count again; their observations
stay voided.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-28
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("purchase_line", sa.Column("removed_at", sa.DateTime(timezone=True)))
    op.add_column(
        "purchase_line",
        sa.Column(
            "removed_by",
            UUID(as_uuid=True),
            sa.ForeignKey("app_user.id", name="fk_purchase_line_removed_by"),
        ),
    )
    op.create_check_constraint(
        "ck_purchase_line_removed",
        "purchase_line",
        "(removed_at IS NULL) = (removed_by IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_purchase_line_removed", "purchase_line", type_="check")
    op.drop_constraint("fk_purchase_line_removed_by", "purchase_line", type_="foreignkey")
    op.drop_column("purchase_line", "removed_by")
    op.drop_column("purchase_line", "removed_at")
