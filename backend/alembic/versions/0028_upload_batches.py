"""Upload batches (04, 2C; issue 122): which receipts were uploaded together.

``upload_batch`` is one upload: one receipt, or several chosen or dropped at
once. ``upload_batch_receipt`` records what became of each file in it: read as
``new``, read again after a removal (``revived``), or ``already_seen`` and left
alone. A receipt uploaded again later belongs to both batches, which is why this
is a table of its own rather than a column on the job.

``ingest_job.uploaded_at`` is when the receipt was last sent to be read. A
revived receipt's job keeps its ``created_at`` (the first upload) but takes a
new ``uploaded_at``, so lists show today's date for it.

Existing jobs are grouped into batches by uploader, a new batch starting after
a gap of more than five minutes, so the upload history has no hole before this
migration.

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-06
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

OUTCOMES = ("new", "revived", "already_seen")


def upgrade() -> None:
    op.create_table(
        "upload_batch",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("app_user.id"), nullable=True),
        # How many files the person chose; uploads that fail never reach a row.
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("file_count > 0", name="ck_upload_batch_file_count"),
    )
    op.create_index("ix_upload_batch_created_at", "upload_batch", ["created_at"])
    op.create_table(
        "upload_batch_receipt",
        sa.Column(
            "batch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("upload_batch.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("ingest_job.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            f"outcome IN ({', '.join(repr(o) for o in OUTCOMES)})",
            name="ck_upload_batch_receipt_outcome",
        ),
    )
    op.create_index("ix_upload_batch_receipt_job", "upload_batch_receipt", ["job_id"])
    op.add_column(
        "ingest_job",
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE ingest_job SET uploaded_at = created_at")
    op.alter_column("ingest_job", "uploaded_at", nullable=False, server_default=sa.func.now())

    # One batch per run of uploads by the same person with no gap over 5 minutes.
    op.execute(
        """
        CREATE TEMP TABLE _runs ON COMMIT DROP AS
        WITH ordered AS (
            SELECT j.id AS job_id, j.created_at, d.uploaded_by,
                   CASE WHEN j.created_at - lag(j.created_at) OVER w > interval '5 minutes'
                             OR lag(j.created_at) OVER w IS NULL
                        THEN 1 ELSE 0 END AS starts
            FROM ingest_job j
            JOIN receipt_document d ON d.id = j.receipt_document_id
            WINDOW w AS (PARTITION BY d.uploaded_by ORDER BY j.created_at, j.id)
        )
        SELECT job_id, created_at, uploaded_by,
               sum(starts) OVER (PARTITION BY uploaded_by ORDER BY created_at, job_id) AS run
        FROM ordered
        """
    )
    op.execute(
        """
        CREATE TEMP TABLE _batches ON COMMIT DROP AS
        SELECT gen_random_uuid() AS id, uploaded_by, run,
               min(created_at) AS created_at, count(*) AS file_count
        FROM _runs GROUP BY uploaded_by, run
        """
    )
    op.execute(
        """
        INSERT INTO upload_batch (id, created_by, file_count, created_at)
        SELECT id, uploaded_by, file_count, created_at FROM _batches
        """
    )
    op.execute(
        """
        INSERT INTO upload_batch_receipt (batch_id, job_id, outcome, created_at)
        SELECT b.id, r.job_id, 'new', r.created_at
        FROM _runs r JOIN _batches b ON b.uploaded_by = r.uploaded_by AND b.run = r.run
        """
    )


def downgrade() -> None:
    op.drop_column("ingest_job", "uploaded_at")
    op.drop_index("ix_upload_batch_receipt_job", table_name="upload_batch_receipt")
    op.drop_table("upload_batch_receipt")
    op.drop_index("ix_upload_batch_created_at", table_name="upload_batch")
    op.drop_table("upload_batch")
