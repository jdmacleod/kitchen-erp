"""Image lookups for the products helper (clip quality, CQ4; 2026-10-05).

A clip whose images the browser could not read (another host that refuses it) can
hand up to four of their addresses to the lookup helper, which fetches them. The
request kind ``image`` joins ``gtin``, ``page`` and ``cutout``.

Downgrade keeps every answer (lookup_answer is append-only and refers to its
request): open or answered image requests become closed page requests.

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-05
"""

from __future__ import annotations

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_lookup_request_kind", "lookup_request", type_="check")
    op.create_check_constraint(
        "ck_lookup_request_kind", "lookup_request", "kind IN ('gtin', 'page', 'cutout', 'image')"
    )


def downgrade() -> None:
    op.execute("UPDATE lookup_request SET kind = 'page', status = 'closed' WHERE kind = 'image'")
    op.drop_constraint("ck_lookup_request_kind", "lookup_request", type_="check")
    op.create_check_constraint(
        "ck_lookup_request_kind", "lookup_request", "kind IN ('gtin', 'page', 'cutout')"
    )
