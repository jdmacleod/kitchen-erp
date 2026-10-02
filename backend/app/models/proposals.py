"""Product captures and proposals (04, 2L)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, Computed, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamped, UUIDPrimaryKey
from app.models.geo import GeographyPoint

CAPTURE_CHANNELS = ("clip", "paste_url", "barcode", "photo", "helper")
PROPOSAL_KINDS = ("new_product", "product_update")
PROPOSAL_STATUSES = ("pending", "accepted", "rejected", "superseded")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class ProductCapture(UUIDPrimaryKey, Base):
    """The evidence for a proposal. Append-only, apart from purging ``payload.dom_text``."""

    __tablename__ = "product_capture"
    __table_args__ = (
        CheckConstraint(_in("channel", CAPTURE_CHANNELS), name="ck_product_capture_channel"),
    )

    # Of the canonical payload, without the capture time: a retry is the same capture.
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    capture_geo: Mapped[Any | None] = mapped_column(GeographyPoint)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProductProposal(UUIDPrimaryKey, Timestamped, Base):
    """What a capture says a product is, waiting for a person to accept or reject it.

    The optional JSONB columns store SQL NULL, not JSON null, for None: the
    generated keys test ``listing IS NULL``.
    """

    __tablename__ = "product_proposal"
    __table_args__ = (
        CheckConstraint(_in("kind", PROPOSAL_KINDS), name="ck_product_proposal_kind"),
        CheckConstraint(_in("status", PROPOSAL_STATUSES), name="ck_product_proposal_status"),
        CheckConstraint(
            "kind <> 'product_update' OR product_id IS NOT NULL",
            name="ck_product_proposal_update_target",
        ),
        # The pending-only unique indexes on listing_key and gtin_key are in migration 0021.
    )

    capture_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_capture.id"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="new_product")
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    # {field: {value, source, confidence?, alternatives, conflict}}: app.catalog.proposals.merge
    fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # {strong?: {product_id, reason}, candidates: [...], model?: {...}, preselect?}
    match: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # {vendor_id, canonical_url, title, vendor_sku?, store_ref?}
    listing: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    # {amount, qty, unit, is_promo}
    price: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    listing_key: Mapped[str | None] = mapped_column(
        Text, Computed("(listing ->> 'vendor_id') || ' ' || (listing ->> 'canonical_url')")
    )
    gtin_key: Mapped[str | None] = mapped_column(
        Text, Computed("CASE WHEN listing IS NULL THEN fields -> 'gtin' ->> 'value' END")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id")
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
