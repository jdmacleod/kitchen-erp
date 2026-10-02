"""The products helper's queue, its answers, and posted-price changes (04, 2N)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKey


class LookupRequest(UUIDPrimaryKey, Base):
    """Something the helper may look up: a barcode, a page, or a photo's cutout."""

    __tablename__ = "lookup_request"
    __table_args__ = (
        CheckConstraint("kind IN ('gtin', 'page', 'cutout')", name="ck_lookup_request_kind"),
        CheckConstraint(
            "status IN ('open', 'answered', 'closed')", name="ck_lookup_request_status"
        ),
        # The owner-matches-kind check and the one-open-cutout index are in 0023.
    )

    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_proposal.id", ondelete="CASCADE")
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id", ondelete="CASCADE")
    )
    product_image_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_image.id", ondelete="CASCADE")
    )
    # A scheduled refresh of this listing (page requests only, 0024).
    listing_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendor_listing.id", ondelete="CASCADE")
    )
    value: Mapped[str | None] = mapped_column(Text)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id")
    )
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LookupAnswer(UUIDPrimaryKey, Base):
    """Append-only: every answer the helper sent, and what became of it."""

    __tablename__ = "lookup_answer"

    request_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lookup_request.id"), index=True
    )
    token_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    body: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ListingPriceChange(UUIDPrimaryKey, Base):
    """A refreshed posted price, recorded in the price book only if a person accepts it."""

    __tablename__ = "listing_price_change"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected')", name="ck_listing_price_change_status"
        ),
        CheckConstraint("amount >= 0 AND qty > 0", name="ck_listing_price_change_amount"),
    )

    listing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendor_listing.id"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric, nullable=False, default=Decimal("1"))
    unit: Mapped[str] = mapped_column(String(16), ForeignKey("unit.code"), nullable=False)
    is_promo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="pending")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id")
    )
    observation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("price_observation.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
