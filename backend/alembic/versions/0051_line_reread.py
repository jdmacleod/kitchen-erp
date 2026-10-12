"""Re-read proposals for a receipt's lines (04, 2S; #272).

``line_reread`` holds one request to re-read a receipt purchase's lines and the
proposal it produced. It is ordinary mutable state, not a fact: a proposal is
derived from the purchase and a reading and can be made again. A partial unique
index allows at most one open (pending, running or ready) per purchase.

Downgrade drops the table.

Revision ID: 0051
Revises: 0050
Create Date: 2026-10-12
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "line_reread",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "purchase_id",
            UUID(as_uuid=True),
            sa.ForeignKey("purchase.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("proposal", JSONB(), nullable=True),
        sa.Column("purchase_status", sa.String(16), nullable=False),
        sa.Column("lines_fingerprint", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(100), nullable=True),
        sa.Column("requested_by", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=False),
        sa.Column("decided_by", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("mode IN ('check', 'read')", name="ck_line_reread_mode"),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'ready', 'no_changes', 'failed', 'applied', "
            "'dismissed')",
            name="ck_line_reread_status",
        ),
    )
    op.create_index(
        "uq_line_reread_open",
        "line_reread",
        ["purchase_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running', 'ready')"),
    )


def downgrade() -> None:
    op.drop_index("uq_line_reread_open", table_name="line_reread")
    op.drop_table("line_reread")
