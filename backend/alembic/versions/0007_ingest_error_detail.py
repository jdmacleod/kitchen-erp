"""An ingest job records the condition behind its error code, not just the code.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-24
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ingest_job", sa.Column("last_error_detail", sa.Text()))


def downgrade() -> None:
    op.drop_column("ingest_job", "last_error_detail")
