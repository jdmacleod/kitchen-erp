"""Receipts, ingest jobs, purchases, lines, aliases, and the price book (Phase 2)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, column_property, mapped_column, relationship

from app.models.base import Base, Timestamped, UUIDPrimaryKey
from app.models.catalog import Product
from app.models.geo import GeographyPoint, VendorLocation

INGEST_STAGES = ("captured", "ocr", "header", "lines", "resolve", "review", "committed")
INGEST_STATUSES = ("pending", "running", "needs_review", "done", "failed", "discarded")
PURCHASE_STATUSES = ("draft", "reviewed", "committed", "voided")
PURCHASE_SOURCES = ("receipt", "manual", "import")
LINE_KINDS = ("item", "discount", "tax", "deposit", "fee")
RESOLUTIONS = ("barcode", "alias", "fuzzy", "llm", "manual", "unmatched", "ignored")
OBSERVATION_SOURCES = ("receipt", "manual", "shelf", "import", "listing")
NORM_STATUSES = ("ok", "no_density", "unknown_measure", "no_pack", "no_qty")
BRIDGE_KINDS = ("none", "density", "density_override", "measure", "pack")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class ReceiptDocument(UUIDPrimaryKey, Base):
    """Immutable after insert."""

    __tablename__ = "receipt_document"

    sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    image_path: Mapped[str] = mapped_column(Text, nullable=False)
    mime: Mapped[str] = mapped_column(String(100), nullable=False)
    bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    capture_geo: Mapped[Any | None] = mapped_column(GeographyPoint)
    client_ocr_text: Mapped[str | None] = mapped_column(Text)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class IngestJob(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "ingest_job"
    __table_args__ = (
        CheckConstraint(_in("stage", INGEST_STAGES), name="ck_ingest_job_stage"),
        CheckConstraint(_in("status", INGEST_STATUSES), name="ck_ingest_job_status"),
    )

    receipt_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("receipt_document.id"), unique=True, nullable=False
    )
    stage: Mapped[str] = mapped_column(String(16), nullable=False, default="captured")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    # The condition behind last_error, when the code alone does not identify it:
    # "ReadTimeout after 120s", "application/pdf". Codes are a closed set the UI
    # maps to sentences; this is the part that varies between two jobs that
    # failed the same way.
    last_error_detail: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(100))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purchase_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase.id", use_alter=True, name="fk_ingest_job_purchase")
    )


class IngestStageResult(UUIDPrimaryKey, Base):
    """Append-only audit trail of every stage attempt."""

    __tablename__ = "ingest_stage_result"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingest_job.id"), nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    adapter: Mapped[str] = mapped_column(String(100), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(50), nullable=False)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Purchase(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "purchase"
    __table_args__ = (
        CheckConstraint(_in("status", PURCHASE_STATUSES), name="ck_purchase_status"),
        CheckConstraint(_in("source", PURCHASE_SOURCES), name="ck_purchase_source"),
        CheckConstraint(
            "status = 'draft' OR vendor_location_id IS NOT NULL", name="ck_purchase_location"
        ),
        CheckConstraint(
            "(status = 'voided') = (voided_at IS NOT NULL) "
            "AND (voided_at IS NULL) = (voided_by IS NULL)",
            name="ck_purchase_voided",
        ),
        Index(
            "uq_purchase_import_ref",
            "import_ref",
            unique=True,
            postgresql_where="import_ref IS NOT NULL",
        ),
    )

    vendor_location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendor_location.id"), index=True
    )
    receipt_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("receipt_document.id")
    )
    purchased_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    subtotal: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    tax: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    total: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    entered_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id"), nullable=False
    )
    flags: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    ledger_txn_ref: Mapped[str | None] = mapped_column(Text)
    import_ref: Mapped[str | None] = mapped_column(Text)
    # Removed after reaching the price book (#74): kept, read-only, prices voided.
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", name="fk_purchase_voided_by")
    )

    # The lines people see and count. A removed line stays in the table only as
    # the provenance of an observation, and nothing that reads a purchase's lines
    # should see it (#72); all_lines is the whole record, for audit.
    lines: Mapped[list[PurchaseLine]] = relationship(
        primaryjoin="and_(Purchase.id == PurchaseLine.purchase_id, "
        "PurchaseLine.removed_at.is_(None))",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="PurchaseLine.seq",
    )
    all_lines: Mapped[list[PurchaseLine]] = relationship(
        viewonly=True, order_by="PurchaseLine.seq", lazy="raise"
    )
    vendor_location: Mapped[VendorLocation | None] = relationship(lazy="joined")


class PurchaseLine(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "purchase_line"
    __table_args__ = (
        CheckConstraint(_in("line_kind", LINE_KINDS), name="ck_purchase_line_kind"),
        CheckConstraint(_in("resolution", RESOLUTIONS), name="ck_purchase_line_resolution"),
        CheckConstraint(
            "line_kind = 'item' OR product_id IS NULL", name="ck_purchase_line_product"
        ),
        UniqueConstraint("purchase_id", "seq", name="uq_purchase_line_seq"),
        CheckConstraint(
            "(removed_at IS NULL) = (removed_by IS NULL)", name="ck_purchase_line_removed"
        ),
    )

    purchase_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_text: Mapped[str | None] = mapped_column(Text)
    raw_text_norm: Mapped[str | None] = mapped_column(Text)
    suggestions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    line_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="item")
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id"), index=True
    )
    parent_line_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_line.id", ondelete="SET NULL")
    )
    qty: Mapped[Decimal | None] = mapped_column(Numeric)
    unit: Mapped[str | None] = mapped_column(String(16), ForeignKey("unit.code"))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    resolution: Mapped[str] = mapped_column(String(16), nullable=False, default="unmatched")
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id")
    )
    resolution_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    flags: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", name="fk_purchase_line_removed_by")
    )

    purchase: Mapped[Purchase] = relationship(viewonly=True)
    product: Mapped[Product | None] = relationship(lazy="joined")


class ReceiptAlias(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "receipt_alias"
    __table_args__ = (
        CheckConstraint(
            "disposition IN ('product', 'ignore')", name="ck_receipt_alias_disposition"
        ),
        CheckConstraint(
            "(disposition = 'product') = (product_id IS NOT NULL)", name="ck_receipt_alias_product"
        ),
        UniqueConstraint("vendor_id", "raw_text_norm", name="uq_receipt_alias_vendor_text"),
    )

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendor.id"), nullable=False
    )
    raw_text_norm: Mapped[str] = mapped_column(Text, nullable=False)
    disposition: Mapped[str] = mapped_column(String(16), nullable=False)
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id")
    )
    confirmed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NamingSuggestion(UUIDPrimaryKey, Timestamped, Base):
    """The model's suggested product for one waiting group (04, 2I; #88).

    One row per vendor and wording, asked for from the naming pass and filled by
    the worker. A suggestion only ever pre-fills a field a person then confirms.
    """

    __tablename__ = "naming_suggestion"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'done', 'failed')", name="ck_naming_suggestion_status"
        ),
        CheckConstraint(
            "ingredient_id IS NULL OR standard_key IS NULL",
            name="ck_naming_suggestion_one_ingredient",
        ),
        UniqueConstraint("vendor_id", "raw_text_norm", name="uq_naming_suggestion_vendor_text"),
        Index(
            "ix_naming_suggestion_pending",
            "created_at",
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
    )

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendor.id", ondelete="CASCADE"), nullable=False
    )
    raw_text_norm: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    name: Mapped[str | None] = mapped_column(Text)
    ingredient_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingredient.id", ondelete="SET NULL")
    )
    standard_key: Mapped[str | None] = mapped_column(String(120))
    error: Mapped[str | None] = mapped_column(String(64))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PriceObservation(UUIDPrimaryKey, Base):
    """Append-only. Corrections are voids plus new observations."""

    __tablename__ = "price_observation"
    __table_args__ = (
        CheckConstraint(_in("source", OBSERVATION_SOURCES), name="ck_price_observation_source"),
        CheckConstraint("price >= 0", name="ck_price_observation_price"),
        CheckConstraint("qty > 0", name="ck_price_observation_qty"),
        CheckConstraint(
            "(source = 'listing') = (listing_id IS NOT NULL)", name="ck_price_observation_listing"
        ),
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id"), nullable=False, index=True
    )
    vendor_location_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendor_location.id"), nullable=False, index=True
    )
    purchase_line_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_line.id"), index=True
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    unit: Mapped[str] = mapped_column(String(16), ForeignKey("unit.code"), nullable=False)
    is_promo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    # The vendor page a posted price came from; set exactly when source = listing (2L).
    listing_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendor_listing.id")
    )
    entered_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    product: Mapped[Product] = relationship(lazy="joined")
    vendor_location: Mapped[VendorLocation] = relationship(lazy="joined")
    norm: Mapped[PriceNorm | None] = relationship(lazy="joined", uselist=False)
    void: Mapped[PriceObservationVoid | None] = relationship(lazy="joined", uselist=False)
    # The purchase a line-derived price came from, so a reader can send someone
    # to correct it there rather than voiding it out from under the purchase.
    purchase_id: Mapped[uuid.UUID | None] = column_property(
        select(PurchaseLine.purchase_id)
        .where(PurchaseLine.id == purchase_line_id)
        .correlate_except(PurchaseLine)
        .scalar_subquery()
    )


class PriceObservationVoid(UUIDPrimaryKey, Base):
    """Append-only."""

    __tablename__ = "price_observation_void"

    observation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("price_observation.id"), unique=True, nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    voided_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id"), nullable=False
    )
    voided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PriceNorm(Base):
    """Derived and rebuildable: normalized unit prices with bridge provenance."""

    __tablename__ = "price_norm"
    __table_args__ = (
        CheckConstraint(_in("status", NORM_STATUSES), name="ck_price_norm_status"),
        CheckConstraint(_in("bridge_kind", BRIDGE_KINDS), name="ck_price_norm_bridge_kind"),
    )

    observation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("price_observation.id", ondelete="CASCADE"), primary_key=True
    )
    canonical_qty: Mapped[Decimal | None] = mapped_column(Numeric)
    norm_unit: Mapped[str | None] = mapped_column(String(16), ForeignKey("unit.code"))
    norm_unit_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    bridge_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="none")
    bridge_source: Mapped[str | None] = mapped_column(String(16))
    bridge_confirmed: Mapped[bool | None] = mapped_column(Boolean)
    convert_version: Mapped[str] = mapped_column(String(16), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
