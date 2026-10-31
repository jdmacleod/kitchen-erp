"""A product's codes on its page, and forgetting one (03, 1H; #448).

Remembering a code takes a person's click, and so does forgetting one. A code that
names the counter rather than the item would otherwise keep identifying the wrong
lines. Forgetting removes the identifier and the suggestions it left on waiting
lines. Lines it already identified keep their product, and their prices stay
where a person can see and correct them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.identifiers import display
from app.core.errors import ApiError
from app.models import PurchaseLine
from app.models.geo import Vendor
from app.services.catalog import get_product


@dataclass(frozen=True)
class ProductCode:
    id: uuid.UUID
    scheme: str
    value: str
    vendor_id: uuid.UUID | None
    vendor_name: str | None
    source: str
    is_barcode: bool  # the one the product's barcode field shows; changed there, not here
    created_at: datetime


async def list_codes(db: AsyncSession, product_id: uuid.UUID) -> list[ProductCode]:
    """Every code the product is known by, its barcode first."""
    product = await get_product(db, product_id)
    shown = product.barcode_identifier
    vendor_ids = {i.vendor_id for i in product.identifiers if i.vendor_id is not None}
    names = (
        dict(
            (
                await db.execute(select(Vendor.id, Vendor.name).where(Vendor.id.in_(vendor_ids)))
            ).all()
        )
        if vendor_ids
        else {}
    )
    codes = [
        ProductCode(
            id=i.id,
            scheme=i.scheme,
            value=display(i.scheme, i.value),
            vendor_id=i.vendor_id,
            vendor_name=names.get(i.vendor_id) if i.vendor_id else None,
            source=i.source,
            is_barcode=i is shown,
            created_at=i.created_at,
        )
        for i in product.identifiers
    ]
    return sorted(codes, key=lambda c: not c.is_barcode)


async def forget_code(db: AsyncSession, product_id: uuid.UUID, code_id: uuid.UUID) -> None:
    """Remove one of the product's codes. The barcode is cleared in the product form."""
    product = await get_product(db, product_id)
    code = next((i for i in product.identifiers if i.id == code_id), None)
    if code is None:
        raise ApiError(404, "not_found", "This product has no such code.")
    if code is product.barcode_identifier:
        raise ApiError(
            409, "barcode_field", "This is the product's barcode. Clear it in Edit product."
        )
    product.identifiers.remove(code)
    await _drop_suggestions(db, product_id, code.value)
    await db.commit()


def _offers_code(suggestion: dict[str, Any], product_id: uuid.UUID, value: str) -> bool:
    # A code suggestion names its code only in its label (code_wording.code_suggestion).
    return (
        suggestion.get("kind") == "code"
        and suggestion.get("product_id") == str(product_id)
        and f"(code {value}" in str(suggestion.get("label", ""))
    )


async def _drop_suggestions(db: AsyncSession, product_id: uuid.UUID, value: str) -> None:
    """Waiting lines stop offering the product through the forgotten code."""
    waiting = await db.scalars(
        select(PurchaseLine).where(
            PurchaseLine.product_id.is_(None),
            PurchaseLine.suggestions.contains([{"kind": "code", "product_id": str(product_id)}]),
        )
    )
    for line in waiting:
        line.suggestions = [s for s in line.suggestions if not _offers_code(s, product_id, value)]
