"""Merged products (03, 1H; #179): ``product.merged_into`` and views that follow it.

A product merged into another stays in place, inactive, with ``merged_into``
naming the survivor. Its price observations are facts and are never touched:
``price_current`` and ``price_current_all`` report each observation under the
survivor instead (``COALESCE(merged_into, product_id)``), so everything built on
them (offers, cheapest, history, costing) sees one product. ``observed_product_id``
keeps the product the observation was recorded against.

Merges are kept one level deep: merging A into B re-points anything already
merged into A, so the views need only one join.

The downgrade restores 0020's views verbatim and refuses to run while any
product is merged: dropping the column would lose which product each one became.

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

# Dependents first: the order views are dropped in.
VIEW_NAMES = (
    "ingredient_offer",
    "offer_latest_regular",
    "offer_latest",
    "offer_applicable",
    "price_current",
)

# 0020's price_current, frozen, for the downgrade; the views built on it are unchanged.
OLD_CURRENT = """
    CREATE VIEW price_current{suffix} AS
    SELECT o.id AS observation_id, o.product_id, o.vendor_location_id, o.purchase_line_id,
           o.observed_at, o.price, o.qty, o.unit, o.is_promo, o.source, o.entered_by,
           n.canonical_qty, n.norm_unit, n.norm_unit_price, n.status AS norm_status,
           n.bridge_kind, n.bridge_source, n.bridge_confirmed, o.listing_id
    FROM price_observation o
    LEFT JOIN price_norm n ON n.observation_id = o.id
    WHERE NOT EXISTS (SELECT 1 FROM price_observation_void v WHERE v.observation_id = o.id)
      {where_posted}
"""

NEW_CURRENT = """
    CREATE VIEW price_current{suffix} AS
    SELECT o.id AS observation_id, COALESCE(op.merged_into, o.product_id) AS product_id,
           o.vendor_location_id, o.purchase_line_id,
           o.observed_at, o.price, o.qty, o.unit, o.is_promo, o.source, o.entered_by,
           n.canonical_qty, n.norm_unit, n.norm_unit_price, n.status AS norm_status,
           n.bridge_kind, n.bridge_source, n.bridge_confirmed, o.listing_id,
           o.product_id AS observed_product_id
    FROM price_observation o
    JOIN product op ON op.id = o.product_id
    LEFT JOIN price_norm n ON n.observation_id = o.id
    WHERE NOT EXISTS (SELECT 1 FROM price_observation_void v WHERE v.observation_id = o.id)
      {where_posted}
"""


def _rest(suffix: str) -> list[str]:
    """The views built on price_current, as 0020 wrote them."""
    return [
        f"""
        CREATE VIEW offer_applicable{suffix} AS
        SELECT pc.*, vl.id AS applies_to_location_id, v.id AS vendor_id, v.price_scope
        FROM price_current{suffix} pc
        JOIN vendor_location src ON src.id = pc.vendor_location_id
        JOIN vendor v ON v.id = src.vendor_id
        JOIN vendor_location vl
          ON vl.vendor_id = v.id AND vl.active AND (v.price_scope = 'chain' OR vl.id = src.id)
        """,
        f"""
        CREATE VIEW offer_latest{suffix} AS
        SELECT DISTINCT ON (product_id, applies_to_location_id) *
        FROM offer_applicable{suffix}
        ORDER BY product_id, applies_to_location_id, observed_at DESC, observation_id DESC
        """,
        f"""
        CREATE VIEW offer_latest_regular{suffix} AS
        SELECT DISTINCT ON (product_id, applies_to_location_id) *
        FROM offer_applicable{suffix}
        WHERE NOT is_promo
        ORDER BY product_id, applies_to_location_id, observed_at DESC, observation_id DESC
        """,
        f"""
        CREATE VIEW ingredient_offer{suffix} AS
        SELECT p.ingredient_id, ol.applies_to_location_id AS vendor_location_id, ol.vendor_id,
               ol.product_id, p.quality_rating, ol.observation_id, ol.observed_at, ol.price,
               ol.qty, ol.unit, ol.is_promo, ol.canonical_qty, ol.norm_unit, ol.norm_unit_price,
               ol.norm_status, ol.bridge_kind, ol.bridge_confirmed, i.perishability
        FROM offer_latest{suffix} ol
        JOIN product p ON p.id = ol.product_id AND p.active
        JOIN ingredient i ON i.id = p.ingredient_id
        """,
    ]


def _chains(current: str) -> list[str]:
    out = []
    for suffix, where_posted in (("_all", ""), ("", "AND o.source <> 'listing'")):
        out.append(current.format(suffix=suffix, where_posted=where_posted))
        out.extend(_rest(suffix))
    return out


def _drop() -> None:
    for suffix in ("", "_all"):
        for name in VIEW_NAMES:
            op.execute(f"DROP VIEW IF EXISTS {name}{suffix}")


def upgrade() -> None:
    op.add_column(
        "product",
        sa.Column(
            "merged_into",
            UUID(as_uuid=True),
            sa.ForeignKey("product.id", name="fk_product_merged_into"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_product_merged_inactive",
        "product",
        "merged_into IS NULL OR (NOT active AND merged_into <> id)",
    )
    op.create_index(
        "ix_product_merged_into",
        "product",
        ["merged_into"],
        postgresql_where=sa.text("merged_into IS NOT NULL"),
    )
    _drop()
    for ddl in _chains(NEW_CURRENT):
        op.execute(ddl)


def downgrade() -> None:
    merged = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM product WHERE merged_into IS NOT NULL"))
        .scalar_one()
    )
    if merged:
        raise RuntimeError(
            f"{merged} merged product(s) exist; downgrading past 0028 would lose "
            "which product each was merged into."
        )
    _drop()
    for ddl in _chains(OLD_CURRENT):
        op.execute(ddl)
    op.drop_index("ix_product_merged_into", table_name="product")
    op.drop_constraint("ck_product_merged_inactive", "product", type_="check")
    op.drop_column("product", "merged_into")
