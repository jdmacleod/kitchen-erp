"""Geography: place, home_base, vendor, vendor_location; product.exclusive_vendor_id FK.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-21
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


class GeographyPoint(sa.types.UserDefinedType):
    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "geography(Point,4326)"


def _ts() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "place",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("geom", GeographyPoint(), nullable=False),
        sa.Column("lat", sa.Numeric(), nullable=False),
        sa.Column("lon", sa.Numeric(), nullable=False),
        sa.Column("label", sa.String(200)),
        *_ts(),
        sa.CheckConstraint("lat >= -90 AND lat <= 90", name="ck_place_lat"),
        sa.CheckConstraint("lon >= -180 AND lon <= 180", name="ck_place_lon"),
    )
    op.execute("CREATE INDEX ix_place_geom ON place USING gist (geom)")

    op.create_table(
        "home_base",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "place_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("place.id"), nullable=False
        ),
        *_ts(),
        sa.UniqueConstraint("name", name="uq_home_base_name"),
    )

    op.create_table(
        "vendor",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("price_scope", sa.String(16), nullable=False, server_default="location"),
        sa.Column("website", sa.String(500)),
        sa.Column("notes", sa.Text()),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_ts(),
        sa.CheckConstraint(
            "kind IN ('chain', 'independent', 'market', 'stand')", name="ck_vendor_kind"
        ),
        sa.CheckConstraint("price_scope IN ('chain', 'location')", name="ck_vendor_price_scope"),
    )
    op.create_index("uq_vendor_name_lower", "vendor", [sa.text("lower(name)")], unique=True)
    op.execute("CREATE INDEX ix_vendor_name_trgm ON vendor USING gin (lower(name) gin_trgm_ops)")

    op.create_table(
        "vendor_location",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "vendor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("vendor.id"), nullable=False
        ),
        sa.Column(
            "place_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("place.id"), nullable=False
        ),
        sa.Column("home_base_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("home_base.id")),
        sa.Column(
            "parent_location_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vendor_location.id"),
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("address", sa.String(500)),
        sa.Column("osm_type", sa.String(16)),
        sa.Column("osm_id", sa.BigInteger()),
        sa.Column("osm_name", sa.String(200)),
        sa.Column("osm_address", sa.String(500)),
        sa.Column("osm_opening_hours", sa.Text()),
        sa.Column("opening_hours", sa.Text()),
        sa.Column("stop_overhead_min", sa.SmallInteger()),
        sa.Column(
            "receipt_identifiers",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_ts(),
        sa.CheckConstraint(
            "parent_location_id IS DISTINCT FROM id", name="ck_location_parent_self"
        ),
        sa.CheckConstraint(
            "osm_type IS NULL OR osm_type IN ('node', 'way', 'relation')",
            name="ck_location_osm_type",
        ),
        sa.CheckConstraint("(osm_type IS NULL) = (osm_id IS NULL)", name="ck_location_osm_pair"),
        sa.CheckConstraint(
            "stop_overhead_min IS NULL OR stop_overhead_min >= 0", name="ck_location_stop_overhead"
        ),
    )
    op.create_index("ix_vendor_location_vendor_id", "vendor_location", ["vendor_id"])
    op.create_index("ix_vendor_location_home_base_id", "vendor_location", ["home_base_id"])
    op.create_index(
        "ix_vendor_location_parent_location_id", "vendor_location", ["parent_location_id"]
    )
    op.create_index(
        "uq_location_osm",
        "vendor_location",
        ["osm_type", "osm_id"],
        unique=True,
        postgresql_where=sa.text("osm_type IS NOT NULL AND osm_id IS NOT NULL"),
    )

    # The column was created plain by 0003 so the catalog never imports the geo mappers.
    op.create_foreign_key(
        "fk_product_exclusive_vendor", "product", "vendor", ["exclusive_vendor_id"], ["id"]
    )


def downgrade() -> None:
    op.drop_constraint("fk_product_exclusive_vendor", "product", type_="foreignkey")
    op.drop_table("vendor_location")
    op.drop_table("vendor")
    op.drop_table("home_base")
    op.drop_table("place")
