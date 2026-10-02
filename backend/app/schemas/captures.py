"""Barcode lookups and product captures (04, 2L)."""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import Field, model_validator

from app.catalog.identifiers import Symbology
from app.schemas.base import ApiModel
from app.schemas.catalog import ProductOut
from app.schemas.geo import Lat, Lon


class BarcodeLookupIn(ApiModel):
    code: Annotated[str, Field(min_length=4, max_length=32)]
    symbology: Symbology | None = None
    vendor_location_id: uuid.UUID | None = None
    lat: Lat | None = None
    lon: Lon | None = None

    @model_validator(mode="after")
    def _pair(self):
        if (self.lat is None) != (self.lon is None):
            raise ValueError("lat and lon must be given together")
        return self


class WeighedLabel(ApiModel):
    """What a weighed-item label's digits say: its item code, and its price or weight."""

    item: str
    price: str | None
    weight: str | None


class BarcodeLookupOut(ApiModel):
    result: Literal["product", "label", "proposal", "unknown"]
    product: ProductOut | None = None
    proposal_id: uuid.UUID | None = None
    label: WeighedLabel | None = None
    vendor_location_id: uuid.UUID | None = None
    # A weighed-item label with no store given or nearby: ask "Which store is this label from?"
    needs_store: bool = False
