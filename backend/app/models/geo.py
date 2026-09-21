"""Places, home bases, vendors, and vendor locations (Phase 1D).

Coordinates are kept twice on ``place``: as exact ``numeric`` ``lat``/``lon``
columns, which are the values the API reads and writes, and as a PostGIS
``geography(Point, 4326)`` ``geom`` column derived from them at write time, which
is what distance queries use. PostGIS stores doubles internally; keeping the
authoritative pair in ``numeric`` means a coordinate round-trips through the API
unchanged and no float ever appears in Python.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    cast,
    func,
    literal,
)
from sqlalchemy.dialects.postgresql import ARRAY, BIGINT, UUID
from sqlalchemy.orm import Mapped, deferred, mapped_column, relationship
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.types import UserDefinedType

from app.models.base import Base, Timestamped, UUIDPrimaryKey

VENDOR_KINDS = ("chain", "independent", "market", "stand")
PRICE_SCOPES = ("chain", "location")
OSM_TYPES = ("node", "way", "relation")


class GeographyPoint(UserDefinedType):
    """``geography(Point, 4326)``. Never read through the ORM; see module docstring."""

    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "geography(Point,4326)"


def point_expr(lat: Decimal, lon: Decimal) -> ColumnElement[Any]:
    """A bound-parameter geography point for ``lat``/``lon`` (never string-built)."""
    return cast(
        func.ST_SetSRID(
            func.ST_MakePoint(cast(literal(lon), Numeric), cast(literal(lat), Numeric)), 4326
        ),
        GeographyPoint(),
    )


class Place(UUIDPrimaryKey, Timestamped, Base):
    """A shared node type: a home or a shop, so Phase 4 can store drive times between any two."""

    __tablename__ = "place"
    __table_args__ = (
        CheckConstraint("lat >= -90 AND lat <= 90", name="ck_place_lat"),
        CheckConstraint("lon >= -180 AND lon <= 180", name="ck_place_lon"),
    )

    geom = deferred(mapped_column(GeographyPoint(), nullable=False))
    lat: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    lon: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    label: Mapped[str | None] = mapped_column(String(200))


class HomeBase(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "home_base"
    __table_args__ = (UniqueConstraint("name", name="uq_home_base_name"),)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    place_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("place.id"), nullable=False
    )

    place: Mapped[Place] = relationship(lazy="joined")


class Vendor(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "vendor"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('chain', 'independent', 'market', 'stand')", name="ck_vendor_kind"
        ),
        CheckConstraint("price_scope IN ('chain', 'location')", name="ck_vendor_price_scope"),
        Index("uq_vendor_name_lower", func.lower("name"), unique=True),
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    price_scope: Mapped[str] = mapped_column(String(16), nullable=False, default="location")
    website: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    locations: Mapped[list[VendorLocation]] = relationship(back_populates="vendor")


class VendorLocation(UUIDPrimaryKey, Timestamped, Base):
    """A vendor at a place. A location with a parent is a stall inside a market.

    ``osm_name``, ``osm_address`` and ``osm_opening_hours`` snapshot the values as
    last fetched from OpenStreetMap. A refresh overwrites a field only while the
    stored value still equals its snapshot; once the user has edited it, the
    field is theirs and the refresh only advances the snapshot.
    """

    __tablename__ = "vendor_location"
    __table_args__ = (
        CheckConstraint("parent_location_id IS DISTINCT FROM id", name="ck_location_parent_self"),
        CheckConstraint(
            "osm_type IS NULL OR osm_type IN ('node', 'way', 'relation')",
            name="ck_location_osm_type",
        ),
        CheckConstraint("(osm_type IS NULL) = (osm_id IS NULL)", name="ck_location_osm_pair"),
        CheckConstraint(
            "stop_overhead_min IS NULL OR stop_overhead_min >= 0", name="ck_location_stop_overhead"
        ),
        Index(
            "uq_location_osm",
            "osm_type",
            "osm_id",
            unique=True,
            postgresql_where="osm_type IS NOT NULL AND osm_id IS NOT NULL",
        ),
    )

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendor.id"), nullable=False, index=True
    )
    place_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("place.id"), nullable=False
    )
    home_base_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("home_base.id"), index=True
    )
    parent_location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendor_location.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    address: Mapped[str | None] = mapped_column(String(500))
    osm_type: Mapped[str | None] = mapped_column(String(16))
    osm_id: Mapped[int | None] = mapped_column(BIGINT)
    osm_name: Mapped[str | None] = mapped_column(String(200))
    osm_address: Mapped[str | None] = mapped_column(String(500))
    osm_opening_hours: Mapped[str | None] = mapped_column(Text)
    opening_hours: Mapped[str | None] = mapped_column(Text)
    stop_overhead_min: Mapped[int | None] = mapped_column(SmallInteger)
    receipt_identifiers: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    vendor: Mapped[Vendor] = relationship(back_populates="locations", lazy="joined")
    place: Mapped[Place] = relationship(lazy="joined")
    parent: Mapped[VendorLocation | None] = relationship(
        remote_side="VendorLocation.id", foreign_keys=[parent_location_id]
    )
