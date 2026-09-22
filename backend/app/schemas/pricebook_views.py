from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from app.schemas.base import ApiModel, DecimalStr


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
    norm_status: str | None
    age_days: DecimalStr
    stale: bool


class ProductHistory(ApiModel):
    points: list[HistoryPoint]
    latest: list[LatestAtLocation]


class Offer(ApiModel):
    product_id: uuid.UUID
    product_name: str
    brand: str | None
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
    norm_status: str | None
    age_days: DecimalStr
    stale: bool


class OfferList(ApiModel):
    items: list[Offer]
    stale_thresholds: dict[str, int]


class CompareIn(ApiModel):
    ingredient_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    min_quality: int | None = Field(default=None, ge=1, le=5)
    exclude_stale: bool = False
    exclude_promo: bool = False


class CompareCell(ApiModel):
    product_id: uuid.UUID
    product_name: str
    brand: str | None
    quality_rating: int | None
    location_id: uuid.UUID
    location_name: str
    observation_id: uuid.UUID
    observed_at: datetime
    is_promo: bool
    norm_unit_price: DecimalStr
    norm_unit: str
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
    stale_thresholds: dict[str, int]


class RecentPrice(ApiModel):
    observation_id: uuid.UUID
    observed_at: datetime
    price: DecimalStr
    qty: DecimalStr
    unit: str
    is_promo: bool
    norm_unit_price: DecimalStr | None
    norm_unit: str | None
    norm_status: str | None
    product_id: uuid.UUID
    product_name: str
    brand: str | None


class LocationPanel(ApiModel):
    last_visit: datetime | None
    spend: DecimalStr
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
    stale: bool


class CheapestOut(ApiModel):
    items: list[CheapestPin]
    unit: str | None


def as_any(value: Any) -> Any:
    return value
