"""Weekly flyers (18, 2U): a store's circular, each read of it, and its offers.

An offer is never a price observation (18, F4). ``flyer_run`` and ``flyer_offer``
are append-only; ``flyer_offer_link`` may change only its decision.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKey

MECHANISMS = ("hosted_circular", "storefront", "itemized", "image", "region")
RUN_STATUSES = ("ok", "empty", "failed", "not_allowed")
PRICE_BASES = ("each", "per_lb", "per_oz", "multi", "bogo", "percent_off", "dollar_off")
RELEVANCE = ("alias", "purchased", "recipe", "list", "watched")
LINK_STATUSES = ("auto", "suggested", "confirmed", "rejected")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class AdSource(UUIDPrimaryKey, Base):
    """The circular a vendor location publishes, and what the helper needs to read it."""

    __tablename__ = "ad_source"
    __table_args__ = (
        UniqueConstraint("vendor_location_id", name="uq_ad_source_location"),
        CheckConstraint(_in("mechanism", MECHANISMS), name="ck_ad_source_mechanism"),
        CheckConstraint("publish_weekday BETWEEN 1 AND 7", name="ck_ad_source_weekday"),
        CheckConstraint("jsonb_typeof(store_ref) = 'object'", name="ck_ad_source_store_ref"),
        CheckConstraint("failures >= 0", name="ck_ad_source_failures"),
    )

    vendor_location_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendor_location.id", ondelete="CASCADE"), nullable=False
    )
    mechanism: Mapped[str] = mapped_column(String(16), nullable=False)
    adapter: Mapped[str] = mapped_column(String(64), nullable=False)
    # The ids the helper's adapter needs; opaque here.
    store_ref: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # ISO weekday the ad week starts (1 = Monday).
    publish_weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Every offer goes to the inbox first, even with a learned name (2W).
    require_confirmation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Image sources: {"rows", "cols", "overlap"}.
    tiles: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Ad weeks in a row that ended failed; two pause the source.
    failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    paused_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class FlyerRun(UUIDPrimaryKey, Base):
    """Append-only: one helper report on one source's ad week."""

    __tablename__ = "flyer_run"
    __table_args__ = (CheckConstraint(_in("status", RUN_STATUSES), name="ck_flyer_run_status"),)

    ad_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ad_source.id"), nullable=False
    )
    lookup_request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lookup_request.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    adapter_version: Mapped[str] = mapped_column(String(40), nullable=False)
    # The ad week as printed.
    ad_week_from: Mapped[date | None] = mapped_column(Date)
    ad_week_to: Mapped[date | None] = mapped_column(Date)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    offered_count: Mapped[int] = mapped_column(Integer, nullable=False)
    relevant_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FlyerOffer(UUIDPrimaryKey, Base):
    """Append-only: one relevant offer from a run, as printed."""

    __tablename__ = "flyer_offer"
    __table_args__ = (
        CheckConstraint(_in("price_basis", PRICE_BASES), name="ck_flyer_offer_basis"),
        CheckConstraint(_in("relevance", RELEVANCE), name="ck_flyer_offer_relevance"),
    )

    flyer_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("flyer_run.id"), nullable=False, index=True
    )
    name_as_printed: Mapped[str] = mapped_column(Text, nullable=False)
    name_norm: Mapped[str] = mapped_column(Text, nullable=False)
    # The printed text when it isn't English (2W).
    name_original: Mapped[str | None] = mapped_column(Text)
    brand: Mapped[str | None] = mapped_column(Text)
    size_qty: Mapped[Decimal | None] = mapped_column(Numeric)
    size_unit: Mapped[str | None] = mapped_column(String(16), ForeignKey("unit.code"))
    price_basis: Mapped[str] = mapped_column(String(12), nullable=False)
    # Money for each, per_lb, per_oz, multi (for basis_qty items) and bogo.
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    basis_qty: Mapped[Decimal | None] = mapped_column(Numeric)
    # percent_off: the percent; dollar_off: the amount.
    discount: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    requires_loyalty: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    limit_qty: Mapped[Decimal | None] = mapped_column(Numeric)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date] = mapped_column(Date, nullable=False)
    department: Mapped[str | None] = mapped_column(Text)
    item_ref: Mapped[str | None] = mapped_column(Text)
    source_page: Mapped[int | None] = mapped_column(Integer)
    # (x, y, width, height) as fractions of the page.
    source_bbox: Mapped[list[Decimal] | None] = mapped_column(ARRAY(Numeric))
    confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False)
    relevance: Mapped[str] = mapped_column(String(12), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FlyerOfferLink(Base):
    """Which product an offer is, and how that was decided."""

    __tablename__ = "flyer_offer_link"
    __table_args__ = (
        CheckConstraint(_in("status", LINK_STATUSES), name="ck_flyer_offer_link_status"),
    )

    flyer_offer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("flyer_offer.id"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id"), index=True
    )
    resolution: Mapped[str | None] = mapped_column(String(16))
    suggestions: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    size_inherited: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # A person's correction of the printed price or size (2W); the offer stays as printed.
    corrected_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    corrected_size_qty: Mapped[Decimal | None] = mapped_column(Numeric)
    corrected_size_unit: Mapped[str | None] = mapped_column(String(16), ForeignKey("unit.code"))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FlyerOfferCrop(Base):
    """The page region an image or region offer was read from, posted by the helper (2W)."""

    __tablename__ = "flyer_offer_crop"

    flyer_offer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("flyer_offer.id"), primary_key=True
    )
    media_path: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FlyerAlias(UUIDPrimaryKey, Base):
    """A vendor's printed offer name learned on a decision; kept apart from receipt_alias."""

    __tablename__ = "flyer_alias"
    __table_args__ = (
        UniqueConstraint("vendor_id", "name_norm", name="uq_flyer_alias_vendor_name"),
        CheckConstraint("ignore <> (product_id IS NOT NULL)", name="ck_flyer_alias_target"),
    )

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendor.id", ondelete="CASCADE"), nullable=False
    )
    name_norm: Mapped[str] = mapped_column(Text, nullable=False)
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id")
    )
    ignore: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    confirmed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
