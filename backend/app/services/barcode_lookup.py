"""Barcode lookups (04, 2L): the catalog first, then the local USDA branded table.

```
code ─▶ weighed-item label? ─▶ store (given, or one within 150 m) ─▶ rw_item ─▶ product + price
                            └▶ no store: the parsed code, price and weight; "Which store?"
     ─▶ GTIN or other code ─▶ an identifier ─▶ product
                            └▶ unknown GTIN ─▶ USDA branded fields, if loaded ─▶ proposal
```

A weighed-item label never guesses its store (PR10b). Nothing here leaves the
machine: the USDA table is a local read (non-negotiable 9).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.barcodes import RwLayout, as_upca, parse_random_weight
from app.catalog.identifiers import AmbiguousCode, InvalidGtin, classify_barcode
from app.catalog.proposals import Candidate
from app.core.errors import ApiError
from app.models import AppUser, Product, ProductIdentifier
from app.models.geo import Place, Vendor, VendorLocation, point_expr
from app.services import catalog, proposals, usda_branded
from app.units.parse import UnitParseFailure, parse_unit

NEARBY_METRES = 150
_PACK = re.compile(r"(\d+(?:\.\d+)?)\s*(fl\.?\s*oz|kg|mg|g|ml|l|lbs?|oz)\b", re.IGNORECASE)
_METRIC = {"g", "kg", "mg", "ml", "l"}


@dataclass
class Lookup:
    result: str  # product | label | proposal | unknown
    product: Product | None = None
    proposal_id: uuid.UUID | None = None
    label: dict[str, Any] | None = None
    vendor_location_id: uuid.UUID | None = None
    needs_store: bool = False


def pack_from_text(text: str | None) -> dict[str, str] | None:
    """A pack from USDA's package text such as "15.5 oz/439 g"; metric preferred."""
    if not text:
        return None
    found = []
    for qty, unit_text in _PACK.findall(text):
        unit = parse_unit(unit_text)
        if not isinstance(unit, UnitParseFailure):
            found.append({"qty": format(Decimal(qty), "f"), "unit": unit})
    metric = [p for p in found if p["unit"] in _METRIC]
    return (metric or found or [None])[0]


async def _nearby_location(db: AsyncSession, lat: Decimal, lon: Decimal) -> VendorLocation | None:
    point = point_expr(lat, lon)
    return (
        await db.execute(
            select(VendorLocation)
            .join(Place, Place.id == VendorLocation.place_id)
            .where(VendorLocation.active, func.ST_DWithin(Place.geom, point, NEARBY_METRES))
            .order_by(func.ST_Distance(Place.geom, point))
            .limit(1)
        )
    ).scalar_one_or_none()


async def _label(
    db: AsyncSession,
    code: str,
    vendor_location_id: uuid.UUID | None,
    lat: Decimal | None,
    lon: Decimal | None,
) -> Lookup:
    location = None
    if vendor_location_id is not None:
        location = await db.get(VendorLocation, vendor_location_id)
        if location is None:
            raise ApiError(404, "not_found", "No such location.")
    elif lat is not None and lon is not None:
        location = await _nearby_location(db, lat, lon)
    if location is None:
        # Never guess the store (PR10b): its layout decides what the digits mean.
        parsed = parse_random_weight(code)
        assert parsed is not None
        return Lookup(result="label", label=_label_out(parsed), needs_store=True)
    vendor = await db.get(Vendor, location.vendor_id)
    assert vendor is not None
    parsed = parse_random_weight(code, RwLayout.from_json(vendor.rw_layout))
    assert parsed is not None
    owner = (
        await db.execute(
            select(ProductIdentifier.product_id).where(
                ProductIdentifier.scheme == "rw_item",
                ProductIdentifier.value == parsed.item,
                ProductIdentifier.vendor_id == vendor.id,
            )
        )
    ).scalar_one_or_none()
    product = await catalog.get_product(db, owner) if owner else None
    return Lookup(
        result="product" if product else "label",
        product=product,
        label=_label_out(parsed),
        vendor_location_id=location.id,
    )


def _label_out(parsed) -> dict[str, Any]:
    return {
        "item": parsed.item,
        "price": format(parsed.price, "f") if parsed.price is not None else None,
        "weight": format(parsed.weight, "f") if parsed.weight is not None else None,
    }


async def usda_candidates(db: AsyncSession, gtin: str) -> list[Candidate]:
    """The local USDA branded table's fields for a GTIN-14, when it is loaded and knows it."""
    food = await usda_branded.lookup(db, gtin)
    if food is None:
        return []
    out = [Candidate("title", food.description.title(), "usda_branded")]
    if food.brand:
        out.append(Candidate("brand", food.brand, "usda_branded"))
    if food.category:
        out.append(Candidate("category", food.category, "usda_branded"))
    if pack := pack_from_text(food.package_size):
        out.append(Candidate("pack", pack, "usda_branded"))
    return out


async def lookup(
    db: AsyncSession,
    user: AppUser,
    code: str,
    *,
    symbology: str | None = None,
    vendor_location_id: uuid.UUID | None = None,
    lat: Decimal | None = None,
    lon: Decimal | None = None,
) -> Lookup:
    code = code.strip()
    if as_upca(code) is not None:
        return await _label(db, code, vendor_location_id, lat, lon)
    try:
        scheme, value = classify_barcode(code, symbology)  # type: ignore[arg-type]
    except AmbiguousCode as exc:
        raise ApiError(
            422,
            "ambiguous_barcode",
            "These eight digits are a valid EAN-8 and a valid UPC-E; say which in symbology.",
        ) from exc
    except InvalidGtin as exc:
        raise ApiError(422, "invalid_gtin", "That barcode's check digit is wrong.") from exc
    owner = (
        await db.execute(
            select(ProductIdentifier.product_id).where(
                ProductIdentifier.scheme == scheme,
                ProductIdentifier.value == value,
                ProductIdentifier.vendor_id.is_(None),
            )
        )
    ).scalar_one_or_none()
    if owner is not None:
        return Lookup(result="product", product=await catalog.get_product(db, owner))
    if scheme != "gtin":
        return Lookup(result="unknown")
    evidence = proposals.Evidence(
        candidates=[Candidate("gtin", value, "scan"), *await usda_candidates(db, value)]
    )
    created = await proposals.create_capture(
        db,
        user=user,
        channel="barcode",
        payload={"code": value},
        evidence=evidence,
        lat=lat,
        lon=lon,
    )
    return Lookup(result="proposal", proposal_id=created.proposal.id)
