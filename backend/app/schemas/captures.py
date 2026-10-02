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


Address = Annotated[str, Field(min_length=8, max_length=2048)]


class CapturedImage(ApiModel):
    """An image the page's own browser could read (best effort, 2M)."""

    url: Annotated[str, Field(max_length=2048)]
    data_base64: str


class PageCaptureIn(ApiModel):
    """What the bookmarklet collects, or just a pasted address (2M).

    ``dom_text`` and ``structured_data`` are checked for size by the endpoint, so a
    page that is too large gets a 413 naming the field rather than a 422.
    """

    channel: Literal["clip", "paste_url"] = "clip"
    page_url: Address
    canonical_url: Address | None = None
    title: Annotated[str, Field(max_length=500)] | None = None
    meta: dict[str, Annotated[str, Field(max_length=4000)]] = Field(default={}, max_length=300)
    structured_data: list[str] = Field(default=[], max_length=20)
    dom_text: str = ""
    image_urls: list[Annotated[str, Field(max_length=2048)]] = Field(default=[], max_length=12)
    images: list[CapturedImage] = Field(default=[], max_length=4)
    vendor_id: uuid.UUID | None = None
    without_store: bool = False


class AddressIn(ApiModel):
    page_url: Address


class VendorRef(ApiModel):
    id: uuid.UUID
    name: str


class AddressPreviewOut(ApiModel):
    """What an address alone says; nothing is fetched (criterion 88)."""

    vendor: VendorRef | None
    canonical_url: str
    title: str | None
    item_number: str | None
