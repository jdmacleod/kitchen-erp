"""Scheduled listing refreshes for the products helper (04, 2N; decided 2026-10-02).

A ``page`` lookup request may name the listing it refreshes, so the helper can
report a changed posted price against that listing. Only page requests carry one.

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-02
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "lookup_request",
        sa.Column(
            "listing_id",
            UUID(as_uuid=True),
            sa.ForeignKey("vendor_listing.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_lookup_request_listing_page", "lookup_request", "listing_id IS NULL OR kind = 'page'"
    )
    op.create_index(
        "uq_lookup_request_open_listing",
        "lookup_request",
        ["listing_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open' AND listing_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_lookup_request_open_listing", table_name="lookup_request")
    op.drop_constraint("ck_lookup_request_listing_page", "lookup_request", type_="check")
    op.drop_column("lookup_request", "listing_id")
