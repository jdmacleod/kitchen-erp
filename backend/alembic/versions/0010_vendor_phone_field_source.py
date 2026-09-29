"""A location's phone, and where each vendor and location field came from (1F).

``vendor_location.phone`` holds a number as printed or published; matching uses
only its digits. ``field_source`` on vendor and vendor_location records, per
field, the source that last wrote it and the value it wrote, so a refresh, an
import or an accepted suggestion overwrites a field only while a person has not
changed it.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-29
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vendor_location", sa.Column("phone", sa.String(40), nullable=True))
    for table in ("vendor", "vendor_location"):
        op.add_column(
            table,
            sa.Column("field_source", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        )


def downgrade() -> None:
    for table in ("vendor_location", "vendor"):
        op.drop_column(table, "field_source")
    op.drop_column("vendor_location", "phone")
