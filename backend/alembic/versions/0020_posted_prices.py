"""Posted prices (04, 2L; plan PV1): ``source = listing`` and the ``*_all`` view chain.

A price seen on a vendor's web page is recorded as a ``price_observation`` with
``source = listing`` and the ``vendor_listing`` it came from. Posted prices are
kept out of the default views (``price_current`` and everything built on it),
which are what comparison, cheapest, best recent price, costing and receipt
resolution read. A parallel chain, ``price_current_all`` → ``offer_applicable_all``
→ ``offer_latest_all`` / ``offer_latest_regular_all`` → ``ingredient_offer_all``,
includes them for the "Include posted prices" filter.

Both chains come from one template, so they cannot drift. The posted-price filter
is applied before "latest per location", so a newer posted price never hides an
older paid one in the default views.

The downgrade restores 0005's views verbatim (a frozen copy below) and refuses to
run while any posted price exists: dropping the column would lose them.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-01
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

OLD_SOURCES = ("receipt", "manual", "shelf", "import")
NEW_SOURCES = (*OLD_SOURCES, "listing")

# Dependents first: the order views are dropped in.
VIEW_NAMES = (
    "ingredient_offer",
    "offer_latest_regular",
    "offer_latest",
    "offer_applicable",
    "price_current",
)


def _in(values: tuple[str, ...]) -> str:
    return f"source IN ({', '.join(repr(v) for v in values)})"


def _chain(suffix: str, where_posted: str) -> list[str]:
    """One view chain. ``suffix`` is "" or "_all"; ``where_posted`` filters price_current."""
    return [
        f"""
        CREATE VIEW price_current{suffix} AS
        SELECT o.id AS observation_id, o.product_id, o.vendor_location_id, o.purchase_line_id,
               o.observed_at, o.price, o.qty, o.unit, o.is_promo, o.source, o.entered_by,
               n.canonical_qty, n.norm_unit, n.norm_unit_price, n.status AS norm_status,
               n.bridge_kind, n.bridge_source, n.bridge_confirmed, o.listing_id
        FROM price_observation o
        LEFT JOIN price_norm n ON n.observation_id = o.id
        WHERE NOT EXISTS (SELECT 1 FROM price_observation_void v WHERE v.observation_id = o.id)
          {where_posted}
        """,
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


# 0005's views, frozen, for the downgrade.
OLD_VIEWS = [
    """
    CREATE VIEW price_current AS
    SELECT o.id AS observation_id, o.product_id, o.vendor_location_id, o.purchase_line_id,
           o.observed_at, o.price, o.qty, o.unit, o.is_promo, o.source, o.entered_by,
           n.canonical_qty, n.norm_unit, n.norm_unit_price, n.status AS norm_status,
           n.bridge_kind, n.bridge_source, n.bridge_confirmed
    FROM price_observation o
    LEFT JOIN price_norm n ON n.observation_id = o.id
    WHERE NOT EXISTS (SELECT 1 FROM price_observation_void v WHERE v.observation_id = o.id)
    """,
    *_chain("", "")[1:],
]


def _drop(suffix: str) -> None:
    for name in VIEW_NAMES:
        op.execute(f"DROP VIEW IF EXISTS {name}{suffix}")


def upgrade() -> None:
    op.add_column(
        "price_observation",
        sa.Column(
            "listing_id",
            UUID(as_uuid=True),
            sa.ForeignKey("vendor_listing.id", name="fk_price_observation_listing"),
            nullable=True,
        ),
    )
    op.drop_constraint("ck_price_observation_source", "price_observation", type_="check")
    op.create_check_constraint("ck_price_observation_source", "price_observation", _in(NEW_SOURCES))
    op.create_check_constraint(
        "ck_price_observation_listing",
        "price_observation",
        "(source = 'listing') = (listing_id IS NOT NULL)",
    )
    _drop("")
    for ddl in _chain("_all", "") + _chain("", "AND o.source <> 'listing'"):
        op.execute(ddl)


def downgrade() -> None:
    posted = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM price_observation WHERE source = 'listing'"))
        .scalar_one()
    )
    if posted:
        raise RuntimeError(
            f"{posted} posted price(s) exist; downgrading past 0020 would lose them."
        )
    _drop("_all")
    _drop("")
    for ddl in OLD_VIEWS:
        op.execute(ddl)
    op.drop_constraint("ck_price_observation_listing", "price_observation", type_="check")
    op.drop_constraint("ck_price_observation_source", "price_observation", type_="check")
    op.create_check_constraint("ck_price_observation_source", "price_observation", _in(OLD_SOURCES))
    op.drop_column("price_observation", "listing_id")
