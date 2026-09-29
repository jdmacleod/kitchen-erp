"""Vendor suggestions from an outside tool, and their review (spec 03 §1F)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field

from app.schemas.base import ApiModel

SuggestionField = Literal[
    "website",
    "brand",
    "wikidata",
    "phone",
    "address",
    "opening_hours",
    "osm",
    "name",
    "price_scope",
]
MAX_ITEMS = 200
MAX_PENDING = 2000
MAX_BODY_BYTES = 1024 * 1024


class SuggestionIn(ApiModel):
    target: Literal["vendor", "location"]
    # A key from the public vendor export: the vendor's, or "<vendor>/<location>".
    key: str = Field(min_length=1, max_length=250)
    field: SuggestionField
    # What the tool read there, so a person's later edit is noticed; null for empty.
    expected: Any = None
    proposed: Any
    source_url: str = Field(min_length=1, max_length=2000)
    evidence: str | None = Field(default=None, max_length=1000)  # shown as plain text only
    confidence: Decimal | None = None  # a decimal string between 0 and 1


class SuggestionBatchIn(ApiModel):
    tool: str = Field(min_length=1, max_length=100)
    tool_version: str = Field(min_length=1, max_length=50)
    items: list[SuggestionIn] = Field(min_length=1)


class SuggestionBatchOut(ApiModel):
    batch_id: uuid.UUID
    stored: int
    # Duplicates of a pending suggestion, or proposals that already hold.
    collapsed: int


class SuggestionOut(ApiModel):
    id: uuid.UUID
    batch_id: uuid.UUID
    target: Literal["vendor", "location"]
    vendor_id: uuid.UUID
    vendor_name: str
    location_id: uuid.UUID | None
    location_name: str | None
    field: SuggestionField
    expected: Any
    current: Any
    proposed: Any
    # The field no longer holds what the tool expected: accepting needs an override.
    stale: bool
    status: Literal["pending", "accepted", "rejected", "stale"]
    source_url: str
    source_domain: str
    evidence: str | None
    tool: str
    tool_version: str
    created_at: datetime


class SuggestionList(ApiModel):
    items: list[SuggestionOut]


class SuggestionVendorCount(ApiModel):
    vendor_id: uuid.UUID
    name: str
    count: int


class SuggestionSummary(ApiModel):
    """What waits for review: the Vendors page line and the review drawer's picker."""

    count: int
    tools: list[str]
    vendors: list[SuggestionVendorCount]


class SuggestionDecisionIn(ApiModel):
    # Apply a suggestion whose field changed since it was proposed ("Replace with proposed").
    override: bool = False


class SuggestionDecisionOut(ApiModel):
    outcome: Literal["accepted", "rejected", "stale"]
    suggestion: SuggestionOut


class AcceptAllOut(ApiModel):
    accepted: int
    # Changed since proposed: left for "Keep mine" or "Replace with proposed" (design D8).
    skipped_stale: int
