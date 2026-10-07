"""The uploaded file's digest, kept beside the stored one (#221; 2026-10-07).

Receipt photos are now stored without their metadata, so the stored file's
sha256 is no longer the uploaded file's. ``upload_sha256`` keeps the digest of the
bytes as uploaded, so a second upload of the same file is still caught. A receipt
stored before this keeps it null: its ``sha256`` is already the upload's.

Downgrade drops the column. A receipt stored stripped then matches only a second
upload of its stripped bytes, not of the original file.

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-07
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("receipt_document", sa.Column("upload_sha256", sa.String(64), nullable=True))
    op.create_unique_constraint(
        "receipt_document_upload_sha256_key", "receipt_document", ["upload_sha256"]
    )


def downgrade() -> None:
    op.drop_constraint("receipt_document_upload_sha256_key", "receipt_document", type_="unique")
    op.drop_column("receipt_document", "upload_sha256")
