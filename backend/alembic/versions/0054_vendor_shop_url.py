"""A vendor's online shop, when it sells through another site (13, 3.4).

``vendor.shop_url`` holds the address of a storefront on another host, such as a
delivery platform's. A clipped page on that host is matched to the vendor as one
on its website is.

Downgrade drops the column.

Revision ID: 0054
Revises: 0053
Create Date: 2026-10-13
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vendor", sa.Column("shop_url", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("vendor", "shop_url")
