"""Name lookups for the products helper (#184; 2026-10-06).

A branded product with no barcode can ask the lookup helper to search by name. The
request kind ``name`` joins ``gtin``, ``page``, ``cutout`` and ``image``; its value
is the product's public facts (brand, name and size) as JSON.

Downgrade keeps every answer (lookup_answer is append-only and refers to its
request): name requests become closed page requests, as 0025's image requests do.

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-06
"""

from __future__ import annotations

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_lookup_request_kind", "lookup_request", type_="check")
    op.create_check_constraint(
        "ck_lookup_request_kind",
        "lookup_request",
        "kind IN ('gtin', 'page', 'cutout', 'image', 'name')",
    )


def downgrade() -> None:
    op.execute("UPDATE lookup_request SET kind = 'page', status = 'closed' WHERE kind = 'name'")
    op.drop_constraint("ck_lookup_request_kind", "lookup_request", type_="check")
    op.create_check_constraint(
        "ck_lookup_request_kind", "lookup_request", "kind IN ('gtin', 'page', 'cutout', 'image')"
    )
