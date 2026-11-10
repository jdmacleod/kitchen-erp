"""The offer queue (18, 2U-3): suggested flyer offers, grouped by vendor and printed name."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.schemas.base import ApiModel, DecimalStr


class OfferVendor(ApiModel):
    id: uuid.UUID
    name: str


class OfferLocation(ApiModel):
    id: uuid.UUID
    name: str


class OfferSuggestion(ApiModel):
    kind: Literal["alias", "fuzzy", "llm", "similar"]
    product_id: uuid.UUID | None
    ignore: bool = False
    label: str
    score: str | None = None
    brand_note: str | None = None


class QueuedOffer(ApiModel):
    id: uuid.UUID
    location: OfferLocation
    name_as_printed: str
    price_basis: Literal["each", "per_lb", "per_oz", "multi", "bogo", "percent_off", "dollar_off"]
    price: DecimalStr | None
    basis_qty: DecimalStr | None
    discount: DecimalStr | None
    size_qty: DecimalStr | None
    size_unit: str | None
    requires_loyalty: bool
    limit_qty: DecimalStr | None
    valid_from: date
    valid_to: date


class OfferGroup(ApiModel):
    """One vendor's printed name; a decision applies to every store's offer of it."""

    vendor: OfferVendor
    name_norm: str
    name_as_printed: str
    # Asks a person: a watched ingredient, or a source that asks first (F13, 2W).
    watched: bool
    suggestions: list[OfferSuggestion]
    offers: list[QueuedOffer]
    first_seen_at: datetime


class OfferQueue(ApiModel):
    """Watched offers ask a person; the other offers wait quietly (F13)."""

    watched: list[OfferGroup]
    other: list[OfferGroup]


class OfferDecision(ApiModel):
    vendor_id: uuid.UUID
    name_norm: str
    product_id: uuid.UUID | None = None
    ignore: bool = False

    @model_validator(mode="after")
    def _one(self):
        if (self.product_id is not None) == self.ignore:
            raise ValueError("give exactly one of product_id or ignore")
        return self


class OfferDecided(ApiModel):
    decided: int


# --- read models (2U-4) ----------------------------------------------------------------


class OfferProduct(ApiModel):
    id: uuid.UUID
    name: str
    # A list item for the ingredient is on offer when its product is (UI-9.2).
    ingredient_id: uuid.UUID | None = None


class LinkedOffer(ApiModel):
    """A linked offer as printed, with a person's corrections and the size it reads by."""

    id: uuid.UUID
    product: OfferProduct
    vendor: OfferVendor
    location: OfferLocation
    name_as_printed: str
    price_basis: Literal["each", "per_lb", "per_oz", "multi", "bogo", "percent_off", "dollar_off"]
    price: DecimalStr | None
    basis_qty: DecimalStr | None
    discount: DecimalStr | None
    size_qty: DecimalStr | None
    size_unit: str | None
    # The size is the product's: the circular printed none (U16).
    size_inherited: bool
    requires_loyalty: bool
    limit_qty: DecimalStr | None
    valid_from: date
    valid_to: date
    ad_week_from: date | None
    ad_week_to: date | None


class CurrentOffer(LinkedOffer):
    # Per canonical unit; null when the offer can only be shown as printed.
    unit_price: DecimalStr | None
    unit: str | None
    # The trailing regular price a percent or dollar off is taken from.
    regular_unit_price: DecimalStr | None


class CurrentOffers(ApiModel):
    items: list[CurrentOffer]


class OfferHistory(ApiModel):
    items: list[LinkedOffer]


# --- a vendor location's circular (2U-4; UI-9.5) ---------------------------------------

Mechanism = Literal["hosted_circular", "storefront", "itemized", "image", "region"]


class CircularIn(ApiModel):
    """What the helper is told about a location's circular; replaces it whole."""

    mechanism: Mechanism
    adapter: str = Field(min_length=1, max_length=64)
    store_ref: dict[str, Annotated[str, Field(max_length=200)]] = Field(default={}, max_length=8)
    publish_weekday: int = Field(ge=1, le=7)
    enabled: bool = False
    # Left out: image and region sources ask first, the others don't (2W).
    require_confirmation: bool | None = None


class CircularPatch(ApiModel):
    enabled: bool | None = None
    require_confirmation: bool | None = None
    publish_weekday: int | None = Field(default=None, ge=1, le=7)


class CircularSourceOut(ApiModel):
    mechanism: Mechanism
    adapter: str
    store_ref: dict[str, str]
    publish_weekday: int
    enabled: bool
    require_confirmation: bool
    failures: int
    paused_until: datetime | None


class FlyerRunOut(ApiModel):
    id: uuid.UUID
    status: Literal["ok", "empty", "failed", "not_allowed"]
    reason: str | None
    ad_week_from: date | None
    ad_week_to: date | None
    fetched_at: datetime
    offered_count: int
    relevant_count: int


class CircularOut(ApiModel):
    """A location's circular: none set up is ``source: null``."""

    source: CircularSourceOut | None
    # The newest first.
    runs: list[FlyerRunOut]
    # A read is waiting for the helper.
    checking: bool
