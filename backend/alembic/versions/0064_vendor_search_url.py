"""A vendor's search page (04, 2D).

``vendor.search_url`` holds the address of the vendor's search page with ``{q}``
where the words go, so a receipt line waiting to be identified can be looked for
there. The application only builds the link; it never fetches the page.

Downgrade drops the column.

Revision ID: 0064
Revises: 0063
Create Date: 2026-10-19
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vendor", sa.Column("search_url", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("vendor", "search_url")
