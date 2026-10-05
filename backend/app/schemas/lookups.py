"""The products helper contract (04, 2N): the queue and the people's side of it."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.base import ApiModel, DecimalStr


class LookupRequestOut(ApiModel):
    id: uuid.UUID
    kind: Literal["gtin", "page", "cutout"]
    value: str | None
    # Set on a scheduled listing refresh: report changed prices against it.
    listing_id: uuid.UUID | None = None
    status: Literal["open", "answered", "closed"]
    created_at: datetime
    answered_at: datetime | None


class ProductPageLookUp(ApiModel):
    page_url: str = Field(min_length=8, max_length=2048)


class LookupQueue(ApiModel):
    items: list[LookupRequestOut]


class AnswerOut(ApiModel):
    outcome: Literal["merged", "update_opened", "no_change", "closed"]
    proposal_id: uuid.UUID | None


class PricesReported(ApiModel):
    added: int


class HelperStatus(ApiModel):
    """Whether "Look this up online" is offered: a products helper token exists (PD8)."""

    configured: bool


class PriceChangeOut(ApiModel):
    id: uuid.UUID
    amount: DecimalStr
    qty: DecimalStr
    unit: str
    is_promo: bool
    seen_at: datetime
    listing_id: uuid.UUID
    title: str
    canonical_url: str
    product_id: uuid.UUID
    product_name: str
    vendor_id: uuid.UUID
    vendor_name: str
    created_at: datetime


class PriceChangeList(ApiModel):
    items: list[PriceChangeOut]


class PriceChangeDecision(ApiModel):
    vendor_location_id: uuid.UUID | None = None


class PriceChangeDecided(ApiModel):
    id: uuid.UUID
    status: Literal["accepted", "rejected"]
    observation_id: uuid.UUID | None
