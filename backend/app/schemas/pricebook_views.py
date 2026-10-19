from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field

from app.schemas.base import ApiModel, DecimalStr

# Which brands an offer list keeps (16, 2R-3): all, store brands, or national ones.
BrandScope = Literal["all", "store", "national"]
# Which produce an offer list keeps: all, only organic, or only conventional.
OrganicScope = Literal["all", "organic", "conventional"]


class HistoryPoint(ApiModel):
    observation_id: uuid.UUID
    observed_at: datetime
    price: DecimalStr
    qty: DecimalStr
    unit: str
    is_promo: bool
    source: str
    norm_unit_price: DecimalStr | None
    norm_unit: str | None
    # As shown (issue 245): per lb, oz or fl oz (or kg, L), from the server.
    display_unit_price: DecimalStr | None = None
    display_unit: str | None = None
    # Issue 381: the regular price when the receipt's row proves a saving already in
    # what was paid (`price`), and per `display_unit`; null for every other price.
    regular_price: DecimalStr | None = None
    regular_display_unit_price: DecimalStr | None = None
    norm_status: str | None
    location_id: uuid.UUID
    location_name: str
    vendor_id: uuid.UUID
    vendor_name: str
    price_scope: str
    series: str


class LatestAtLocation(ApiModel):
    location_id: uuid.UUID
    location_name: str
    vendor_id: uuid.UUID
    vendor_name: str
    price_scope: str
    observation_id: uuid.UUID
    observed_at: datetime
    price: DecimalStr
    qty: DecimalStr
    unit: str
    is_promo: bool
    norm_unit_price: DecimalStr | None
    norm_unit: str | None
    # As shown (issue 245): per lb, oz or fl oz (or kg, L), from the server.
    display_unit_price: DecimalStr | None = None
    display_unit: str | None = None
    # Issue 381: the regular price when the receipt's row proves a saving already in
    # what was paid (`price`), and per `display_unit`; null for every other price.
    regular_price: DecimalStr | None = None
    regular_display_unit_price: DecimalStr | None = None
    norm_status: str | None
    age_days: DecimalStr
    stale: bool


class ProductHistory(ApiModel):
    points: list[HistoryPoint]
    latest: list[LatestAtLocation]


class IngredientPricePoint(ApiModel):
    observation_id: uuid.UUID
    observed_at: datetime
    norm_unit_price: DecimalStr
    norm_unit: str
    # As shown (issue 245): per lb, oz or fl oz (or kg, L), from the server.
    display_unit_price: DecimalStr | None = None
    display_unit: str | None = None
    # Issue 381: the regular price when the receipt's row proves a saving already in
    # what was paid (`price`), and per `display_unit`; null for every other price.
    regular_price: DecimalStr | None = None
    regular_display_unit_price: DecimalStr | None = None
    is_promo: bool
    source: str
    product_id: uuid.UUID
    product_name: str
    location_id: uuid.UUID
    vendor_id: uuid.UUID
    vendor_name: str


class IngredientPriceHistory(ApiModel):
    """Normalized prices for the ingredient hub's sparkline and range (D22).

    One point per day, oldest first: that day's cheapest price. ``low`` and ``high``
    cover every price in the window.
    """

    days: int
    points: list[IngredientPricePoint]
    # The range across every point in the window; null when there are none.
    low: DecimalStr | None
    high: DecimalStr | None
    # The same range as shown, and the unit every point's display price uses.
    display_low: DecimalStr | None = None
    display_high: DecimalStr | None = None
    display_unit: str | None = None


class Offer(ApiModel):
    product_id: uuid.UUID
    product_name: str
    brand: str | None
    # A store's own brand (16, 2R-3): its tier and family; false for national or unknown.
    brand_tier: str | None = None
    brand_family_name: str | None = None
    is_store_brand: bool = False
    # Marked organic in the product's attributes (produce only).
    organic: bool = False
    quality_rating: int | None
    pack_qty: DecimalStr | None
    pack_unit: str | None
    location_id: uuid.UUID
    location_name: str
    vendor_id: uuid.UUID
    vendor_name: str
    price_scope: str
    observation_id: uuid.UUID
    observed_at: datetime
    price: DecimalStr
    qty: DecimalStr
    unit: str
    is_promo: bool
    norm_unit_price: DecimalStr | None
    norm_unit: str | None
    # As shown (issue 245): per lb, oz or fl oz (or kg, L), from the server.
    display_unit_price: DecimalStr | None = None
    display_unit: str | None = None
    # Issue 381: the regular price when the receipt's row proves a saving already in
    # what was paid (`price`), and per `display_unit`; null for every other price.
    regular_price: DecimalStr | None = None
    regular_display_unit_price: DecimalStr | None = None
    norm_status: str | None
    age_days: DecimalStr
    stale: bool


class OfferList(ApiModel):
    items: list[Offer]
    stale_after_days: int


class CompareIn(ApiModel):
    ingredient_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    min_quality: int | None = Field(default=None, ge=1, le=5)
    exclude_stale: bool = False
    exclude_promo: bool = False
    # Also count posted web prices (2L); they are out of comparison by default.
    include_posted: bool = False
    # Keep all offers, only store brands, or only national and unknown brands (2R-3).
    brand_scope: BrandScope = "all"
    # Keep all offers, only organic ones, or only conventional ones.
    organic_scope: OrganicScope = "all"
    # Only prices at this kitchen's locations (17, 4A, L3); unset, every kitchen's.
    home_base_id: uuid.UUID | None = None


class CompareCell(ApiModel):
    product_id: uuid.UUID
    product_name: str
    brand: str | None
    # A store's own brand (16, 2R-3): its tier and family; false for national or unknown.
    brand_tier: str | None = None
    brand_family_name: str | None = None
    is_store_brand: bool = False
    # Marked organic in the product's attributes (produce only).
    organic: bool = False
    quality_rating: int | None
    location_id: uuid.UUID
    location_name: str
    observation_id: uuid.UUID
    observed_at: datetime
    is_promo: bool
    norm_unit_price: DecimalStr
    norm_unit: str
    # As shown (issue 245): per lb, oz or fl oz (or kg, L), from the server.
    display_unit_price: DecimalStr | None = None
    display_unit: str | None = None
    # Issue 381: the regular price when the receipt's row proves a saving already in
    # what was paid (`price`), and per `display_unit`; null for every other price.
    regular_price: DecimalStr | None = None
    regular_display_unit_price: DecimalStr | None = None
    stale: bool
    cheapest: bool


class CompareRow(ApiModel):
    ingredient_id: uuid.UUID
    ingredient_name: str
    canonical_unit: str
    cells: dict[str, CompareCell]  # keyed by vendor id; missing key = nothing known


class VendorColumn(ApiModel):
    id: uuid.UUID
    name: str


class CompareOut(ApiModel):
    vendors: list[VendorColumn]
    rows: list[CompareRow]
    stale_after_days: int


class RecentPrice(ApiModel):
    observation_id: uuid.UUID
    observed_at: datetime
    price: DecimalStr
    qty: DecimalStr
    unit: str
    is_promo: bool
    norm_unit_price: DecimalStr | None
    norm_unit: str | None
    # As shown (issue 245): per lb, oz or fl oz (or kg, L), from the server.
    display_unit_price: DecimalStr | None = None
    display_unit: str | None = None
    norm_status: str | None
    product_id: uuid.UUID
    product_name: str
    brand: str | None


class LocationPanel(ApiModel):
    last_visit: datetime | None
    spend: DecimalStr
    # The part of spend that went on kitchen supplies (04, 2T).
    supply_spend: DecimalStr = Decimal("0")
    visits: int
    period_days: int
    recent: list[RecentPrice]


class CheapestPin(ApiModel):
    location_id: uuid.UUID
    location_name: str
    lat: DecimalStr
    lon: DecimalStr
    vendor_id: uuid.UUID
    vendor_name: str
    kind: str
    product_id: uuid.UUID
    product_name: str
    quality_rating: int | None
    observation_id: uuid.UUID
    observed_at: datetime
    is_promo: bool
    norm_unit_price: DecimalStr
    norm_unit: str
    # As shown (issue 245): per lb, oz or fl oz (or kg, L), from the server.
    display_unit_price: DecimalStr | None = None
    display_unit: str | None = None
    stale: bool


class CheapestOut(ApiModel):
    items: list[CheapestPin]
    unit: str | None
    # The unit the pins' display prices read in (issue 245).
    display_unit: str | None = None


def as_any(value: Any) -> Any:
    return value
