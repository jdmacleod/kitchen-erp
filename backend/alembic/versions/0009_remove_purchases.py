"""A purchase can be removed, and a receipt with it (#74).

A purchase that reached the price book is voided: status ``voided`` with who and
when, its prices voided. One that never did is deleted, and its receipt's job
becomes ``discarded`` so the Receipts page stops showing it.

Downgrading has no such states to keep: a voided purchase becomes ``reviewed``
(reopened, its prices still voided) and a discarded job ``failed`` with the
error ``receipt_removed``.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-28
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

JOB_STATUSES = "status IN ('pending', 'running', 'needs_review', 'done', 'failed'{})"
PURCHASE_STATUSES = "status IN ('draft', 'reviewed', 'committed'{})"


def upgrade() -> None:
    op.drop_constraint("ck_ingest_job_status", "ingest_job", type_="check")
    op.create_check_constraint(
        "ck_ingest_job_status", "ingest_job", JOB_STATUSES.format(", 'discarded'")
    )
    op.drop_constraint("ck_purchase_status", "purchase", type_="check")
    op.create_check_constraint(
        "ck_purchase_status", "purchase", PURCHASE_STATUSES.format(", 'voided'")
    )
    op.add_column("purchase", sa.Column("voided_at", sa.DateTime(timezone=True)))
    op.add_column(
        "purchase",
        sa.Column(
            "voided_by",
            UUID(as_uuid=True),
            sa.ForeignKey("app_user.id", name="fk_purchase_voided_by"),
        ),
    )
    op.create_check_constraint(
        "ck_purchase_voided",
        "purchase",
        "(status = 'voided') = (voided_at IS NOT NULL) "
        "AND (voided_at IS NULL) = (voided_by IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_purchase_voided", "purchase", type_="check")
    op.execute("UPDATE purchase SET status = 'reviewed' WHERE status = 'voided'")
    op.execute(
        "UPDATE ingest_job SET status = 'failed', last_error = 'receipt_removed' "
        "WHERE status = 'discarded'"
    )
    op.drop_constraint("fk_purchase_voided_by", "purchase", type_="foreignkey")
    op.drop_column("purchase", "voided_by")
    op.drop_column("purchase", "voided_at")
    op.drop_constraint("ck_purchase_status", "purchase", type_="check")
    op.create_check_constraint("ck_purchase_status", "purchase", PURCHASE_STATUSES.format(""))
    op.drop_constraint("ck_ingest_job_status", "ingest_job", type_="check")
    op.create_check_constraint("ck_ingest_job_status", "ingest_job", JOB_STATUSES.format(""))
