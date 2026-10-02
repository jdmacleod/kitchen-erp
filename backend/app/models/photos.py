"""Product photos and the product work queue (03, 1I)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamped, UUIDPrimaryKey

IMAGE_SOURCES = ("user_photo", "manufacturer", "open_food_facts", "vendor_listing")
IMAGE_ROLES = ("product", "label_front", "label_nutrition", "label_ingredients", "shelf_tag")
IMAGE_STATUSES = ("processing", "candidate", "active", "hidden", "failed")
CUTOUT_SOURCES = ("device", "tool")
JOB_KINDS = ("extract", "resolve", "image_process", "identify")
JOB_STATUSES = ("pending", "running", "done", "failed")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class ProductImage(UUIDPrimaryKey, Timestamped, Base):
    """One photo of a product. Files live under MEDIA_PATH, addressed by ``sha256``."""

    __tablename__ = "product_image"
    __table_args__ = (
        CheckConstraint(_in("source_kind", IMAGE_SOURCES), name="ck_product_image_source"),
        CheckConstraint(_in("role", IMAGE_ROLES), name="ck_product_image_role"),
        CheckConstraint(_in("status", IMAGE_STATUSES), name="ck_product_image_status"),
        CheckConstraint(
            f"cutout_source IS NULL OR {_in('cutout_source', CUTOUT_SOURCES)}",
            name="ck_product_image_cutout_source",
        ),
        CheckConstraint(
            "(mask_sha256 IS NULL) = (cutout_source IS NULL)", name="ck_product_image_mask_pair"
        ),
        CheckConstraint(
            "product_id IS NOT NULL OR proposal_id IS NOT NULL", name="ck_product_image_owner"
        ),
        UniqueConstraint("product_id", "upload_sha256", name="uq_product_image_upload"),
        UniqueConstraint("proposal_id", "upload_sha256", name="uq_product_image_proposal_upload"),
        # The person's choice of main photo: at most one per product.
        Index(
            "uq_product_image_one_pinned",
            "product_id",
            unique=True,
            postgresql_where=text("pinned"),
        ),
    )

    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product.id", ondelete="CASCADE"), index=True
    )
    # A candidate photo belongs to a proposal until it is accepted (2L, PR13).
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_proposal.id", ondelete="CASCADE"), index=True
    )
    # Plain column like product.exclusive_vendor_id: the catalog doesn't import the geo mappers.
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    upload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    source_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    attribution: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(24), nullable=False, default="product")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="processing")
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    phash: Mapped[int | None] = mapped_column(BigInteger)
    mask_sha256: Mapped[str | None] = mapped_column(String(64))
    cutout_source: Mapped[str | None] = mapped_column(String(8))
    is_stock_suspect: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ocr_text: Mapped[str | None] = mapped_column(Text)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProductJob(UUIDPrimaryKey, Timestamped, Base):
    """Product work for the worker, claimed after receipts and before name suggestions."""

    __tablename__ = "product_job"
    __table_args__ = (
        CheckConstraint(_in("kind", JOB_KINDS), name="ck_product_job_kind"),
        CheckConstraint(_in("status", JOB_STATUSES), name="ck_product_job_status"),
        Index(
            "ix_product_job_claimable",
            "created_at",
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
    )

    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    product_capture_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_capture.id"), index=True
    )
    product_image_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_image.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(64))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(200))


class ProductStageResult(UUIDPrimaryKey, Base):
    """Append-only record of each stage a product job ran (like ``ingest_stage_result``)."""

    __tablename__ = "product_stage_result"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("product_job.id"), nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    adapter: Mapped[str] = mapped_column(String(100), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(50), nullable=False)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
