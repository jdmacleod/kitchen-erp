"""What an outside tool proposed about a vendor or location (spec 03 §1F).

Append-only apart from the decision: the runtime role may update only
``status``, ``decided_by`` and ``decided_at``, and a trigger refuses any other
change and every delete (migration 0012).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKey

SUGGESTION_FIELDS = (
    "website",
    "brand",
    "wikidata",
    "phone",
    "address",
    "opening_hours",
    "osm",
    "name",
    "price_scope",
)
VENDOR_FIELDS = ("website", "brand", "wikidata", "name", "price_scope")
LOCATION_FIELDS = ("phone", "address", "opening_hours", "osm", "name")


class VendorSuggestion(UUIDPrimaryKey, Base):
    __tablename__ = "vendor_suggestion"

    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target: Mapped[str] = mapped_column(String(16), nullable=False)
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("vendor.id"))
    vendor_location_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendor_location.id")
    )
    field: Mapped[str] = mapped_column(String(32), nullable=False)
    old_value: Mapped[Any] = mapped_column(JSONB)
    proposed_value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    source_url: Mapped[str] = mapped_column(String(2000), nullable=False)
    evidence: Mapped[str | None] = mapped_column(String(1000))
    tool: Mapped[str] = mapped_column(String(100), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(50), nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    created_by_token_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("api_token.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
