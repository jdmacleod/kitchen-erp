"""``kitchen-erp-products/1``: what the products helper sends back (04, 2N).

The helper is a separate program (the private ``kitchen-erp-products`` repository)
that does the outbound work this application never does. Its answers are
validated like a model's reply: anything unknown is refused, and a confidence
over its source's cap is refused. ``docs/api/kitchen-erp-products-1.schema.json``
is generated from these models, and a contract test holds the two together; the
helper's repository runs the same check against the same file.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

FORMAT = "kitchen-erp-products/1"

# What the helper may say a value came from. Never "person" or "scan": those are
# the household's own evidence.
HelperSource = Literal["manufacturer", "usda_branded", "page_data", "page_meta", "adapter", "model"]
PhotoSourceKind = Literal["manufacturer", "open_food_facts", "vendor_listing"]
PhotoRole = Literal["product", "label_front", "label_nutrition", "label_ingredients", "shelf_tag"]
Url = Annotated[str, Field(max_length=2048, pattern=r"^https?://")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PriceValue(_Strict):
    """A price for a quantity: 0.69 for 1 lb. A bare number is a price for 1 each."""

    amount: Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=4)]
    qty: Annotated[Decimal, Field(gt=0)]
    unit: Annotated[str, Field(max_length=16)]


class AnswerCandidate(_Strict):
    field: Annotated[str, Field(max_length=40)]
    value: str | bool | Decimal | PriceValue | dict[str, str | Decimal]
    source: HelperSource
    confidence: Annotated[Decimal, Field(ge=0, le=1)] | None = None
    source_url: Url | None = None


class AnswerPhoto(_Strict):
    data_base64: Annotated[str, Field(max_length=8_000_000)]
    source_kind: PhotoSourceKind
    role: PhotoRole = "product"
    attribution: Annotated[str, Field(max_length=500)] | None = None
    source_url: Url | None = None
    # A cutout mask for this photo, at its size: a single-channel PNG.
    mask_base64: Annotated[str, Field(max_length=8_000_000)] | None = None


class HelperAnswer(_Strict):
    """The helper's answer to one lookup request."""

    format: Literal["kitchen-erp-products/1"]
    request_id: uuid.UUID
    found: bool = True
    # Why nothing was found, when the helper knows: ``unreachable`` means the page
    # never loaded (network failures or a store that refuses the helper), which can
    # pause the vendor's listing refreshes (#264). Older helpers leave it out.
    reason: Literal["unreachable"] | None = None
    candidates: Annotated[list[AnswerCandidate], Field(max_length=100)] = []
    photos: Annotated[list[AnswerPhoto], Field(max_length=4)] = []


class ListingPrice(_Strict):
    listing_id: uuid.UUID
    amount: Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=4)]
    qty: Annotated[Decimal, Field(gt=0)] = Decimal("1")
    unit: Annotated[str, Field(max_length=16)] = "each"
    is_promo: bool = False
    seen_at: datetime


class ListingPriceReport(_Strict):
    """Posted prices the helper saw change; each waits for a person (PR2)."""

    format: Literal["kitchen-erp-products/1"]
    prices: Annotated[list[ListingPrice], Field(max_length=500)]


def contract_schema() -> dict[str, Any]:
    """The checked-in contract: both message shapes, by name."""
    return {
        "$id": FORMAT,
        "title": "kitchen-erp-products/1",
        "answer": HelperAnswer.model_json_schema(),
        "listing_prices": ListingPriceReport.model_json_schema(),
    }
