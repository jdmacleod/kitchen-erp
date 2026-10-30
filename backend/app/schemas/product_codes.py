from __future__ import annotations

import uuid
from datetime import datetime

from app.schemas.base import ApiModel


class ProductCodeOut(ApiModel):
    """One code a product is known by (03, 1H); vendor-scoped schemes name their vendor."""

    id: uuid.UUID
    scheme: str
    value: str
    vendor_id: uuid.UUID | None
    vendor_name: str | None
    source: str
    # The code the product's barcode field shows; it is changed in the form, not forgotten.
    is_barcode: bool
    created_at: datetime


class ProductCodeList(ApiModel):
    items: list[ProductCodeOut]
