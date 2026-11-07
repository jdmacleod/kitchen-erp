"""The products helper contract (04, 2N): the queue and the people's side of it."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import Field

from app.schemas.base import ApiModel, DecimalStr


class FlyerSourceOut(ApiModel):
    """What the helper needs to read a source's circular (18, 2U)."""

    mechanism: Literal["hosted_circular", "storefront", "itemized", "image", "region"]
    adapter: str
    store_ref: dict[str, str]
    tiles: dict[str, Any] | None
    ad_week: date


class LookupRequestOut(ApiModel):
    id: uuid.UUID
    kind: Literal["gtin", "page", "cutout", "image", "name", "flyer"]
    value: str | None
    # Set on a scheduled listing refresh: report changed prices against it.
    listing_id: uuid.UUID | None = None
    # Set on a flyer request: the source and the ad week to read (18, 2U).
    flyer: FlyerSourceOut | None = None
    status: Literal["open", "answered", "closed"]
    created_at: datetime
    answered_at: datetime | None


class ProductPageLookUp(ApiModel):
    # Without a page, the product's barcode is looked up (its photo, name and size).
    page_url: str | None = Field(default=None, min_length=8, max_length=2048)
    # A branded product with no barcode: search by its brand, name and size (#184).
    by_name: bool = False


class LookupQueue(ApiModel):
    items: list[LookupRequestOut]


class AnswerOut(ApiModel):
    outcome: Literal["merged", "update_opened", "no_change", "closed"]
    proposal_id: uuid.UUID | None


class PricesReported(ApiModel):
    added: int


class FlyerReported(ApiModel):
    run_id: uuid.UUID
    status: Literal["ok", "empty", "failed", "not_allowed"]
    offered: int
    relevant: int
    # Each candidate's offer, in the report's order; null where it wasn't relevant.
    offer_ids: list[uuid.UUID | None]


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
