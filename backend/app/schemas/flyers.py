"""The offer queue (18, 2U-3): suggested flyer offers, grouped by vendor and printed name."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import model_validator

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
