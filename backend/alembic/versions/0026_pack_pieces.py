"""Pieces in a pack (2026-10-05).

A pack may state its pieces beside its size: 19 oz holding 5 links, 6 x 330 ml.
``product.pack_count`` is the number of pieces (the size stays the total) and
``piece_name`` an optional singular noun. For an ingredient measured in pieces, one
pack converts to its pieces: the price book's new bridge kind ``pack_count``.

Downgrade drops the columns and the derived price rows that crossed the new bridge;
``kerp recompute-norms`` rebuilds them (price_norm is derived, non-negotiable 5).

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-05
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

_KINDS = "'none', 'density', 'density_override', 'measure', 'pack'"


def upgrade() -> None:
    op.add_column("product", sa.Column("pack_count", sa.Integer(), nullable=True))
    op.add_column("product", sa.Column("piece_name", sa.String(32), nullable=True))
    op.create_check_constraint(
        "ck_product_pack_count",
        "product",
        "pack_count IS NULL OR (pack_count > 0 AND pack_qty IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_product_piece_name", "product", "piece_name IS NULL OR pack_count IS NOT NULL"
    )
    op.drop_constraint("ck_price_norm_bridge_kind", "price_norm", type_="check")
    op.create_check_constraint(
        "ck_price_norm_bridge_kind", "price_norm", f"bridge_kind IN ({_KINDS}, 'pack_count')"
    )


def downgrade() -> None:
    op.execute("DELETE FROM price_norm WHERE bridge_kind = 'pack_count'")
    op.drop_constraint("ck_price_norm_bridge_kind", "price_norm", type_="check")
    op.create_check_constraint(
        "ck_price_norm_bridge_kind", "price_norm", f"bridge_kind IN ({_KINDS})"
    )
    op.drop_constraint("ck_product_piece_name", "product", type_="check")
    op.drop_constraint("ck_product_pack_count", "product", type_="check")
    op.drop_column("product", "piece_name")
    op.drop_column("product", "pack_count")
