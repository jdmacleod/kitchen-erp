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
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


PriceBasis = Literal["each", "per_lb", "per_oz", "multi", "bogo", "percent_off", "dollar_off"]
Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=4)]
ShortText = Annotated[str, Field(min_length=1, max_length=300)]


class FlyerCandidate(_Strict):
    """One offer as a circular prints it (18). Kitchen ERP decides whether it matters."""

    name_as_printed: ShortText
    # The printed text when the helper translated it (2W).
    name_original: ShortText | None = None
    brand: Annotated[str, Field(max_length=200)] | None = None
    size_qty: Annotated[Decimal, Field(gt=0)] | None = None
    size_unit: Annotated[str, Field(max_length=16)] | None = None
    price_basis: PriceBasis
    # Money for each, per_lb, per_oz, multi (for basis_qty items) and bogo.
    price: Money | None = None
    basis_qty: Annotated[Decimal, Field(gt=0)] | None = None
    # percent_off: the percent; dollar_off: the amount.
    discount: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=4)] | None = None
    requires_loyalty: bool = False
    limit_qty: Annotated[Decimal, Field(gt=0)] | None = None
    valid_from: date
    valid_to: date
    department: Annotated[str, Field(max_length=100)] | None = None
    item_ref: Annotated[str, Field(max_length=100)] | None = None
    source_page: Annotated[int, Field(ge=1, le=200)] | None = None
    # (x, y, width, height) as fractions of the page.
    source_bbox: (
        Annotated[list[Annotated[Decimal, Field(ge=0, le=1)]], Field(min_length=4, max_length=4)]
        | None
    ) = None
    confidence: Annotated[Decimal, Field(ge=0, le=1)]

    @model_validator(mode="after")
    def _consistent(self) -> FlyerCandidate:
        off = self.price_basis in ("percent_off", "dollar_off")
        if off != (self.discount is not None) or off == (self.price is not None):
            raise ValueError(
                "percent_off and dollar_off carry a discount; every other basis a price"
            )
        if (self.price_basis == "multi") != (self.basis_qty is not None):
            raise ValueError("basis_qty is given for multi, and only for multi")
        if (self.size_qty is None) != (self.size_unit is None):
            raise ValueError("size_qty and size_unit come together")
        if self.valid_from > self.valid_to:
            raise ValueError("valid_from is after valid_to")
        return self


FlyerStatus = Literal["ok", "empty", "failed", "not_allowed"]


class FlyerReport(_Strict):
    """The helper's report on one ``flyer`` request: its status and what it read."""

    format: Literal["kitchen-erp-products/1"]
    request_id: uuid.UUID
    status: FlyerStatus
    # Why, for failed and not_allowed: shown to a person, so plain words.
    reason: Annotated[str, Field(max_length=500)] | None = None
    adapter_version: Annotated[str, Field(min_length=1, max_length=40)]
    ad_week_from: date | None = None
    ad_week_to: date | None = None
    fetched_at: datetime
    candidates: Annotated[list[FlyerCandidate], Field(max_length=2000)] = []

    @model_validator(mode="after")
    def _consistent(self) -> FlyerReport:
        if self.status in ("failed", "not_allowed") and not self.reason:
            raise ValueError("a failed or not_allowed report says why")
        if self.status != "ok" and self.candidates:
            raise ValueError("only an ok report carries candidates")
        if self.status == "ok" and not self.candidates:
            raise ValueError("an ok report with no candidates is empty")
        if self.status == "ok" and (self.ad_week_from is None or self.ad_week_to is None):
            raise ValueError("an ok report gives its ad week")
        if self.ad_week_from and self.ad_week_to and self.ad_week_from > self.ad_week_to:
            raise ValueError("ad_week_from is after ad_week_to")
        return self


def contract_schema() -> dict[str, Any]:
    """The checked-in contract: both message shapes, by name."""
    return {
        "$id": FORMAT,
        "title": "kitchen-erp-products/1",
        "answer": HelperAnswer.model_json_schema(),
        "listing_prices": ListingPriceReport.model_json_schema(),
        "flyer_report": FlyerReport.model_json_schema(),
    }
