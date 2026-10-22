"""Weighed-item codes that may name a counter, not an item (04, 2K; #448).

A weighed-item label's item code is read with the vendor's layout. At some
counters several items print the same code, and the digit that tells them apart
falls outside the layout. So a weighed code identifies a line on its own only
when the line's wording is already known for that product at that vendor; any
other line keeps the match as a suggestion for a person to confirm. GTINs,
PLUs and vendor SKUs name one item and are not checked.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import receipt_codes
from app.models import Product, ProductIdentifier, Purchase, PurchaseLine, ReceiptAlias
from app.models.geo import Vendor, VendorLocation
from app.services.normalize import normalize_receipt_text

RW = "rw_item"


def wording_of(line: PurchaseLine) -> str:
    return line.raw_text_norm or normalize_receipt_text(line.raw_text or "")


def prints_code(line: PurchaseLine, vendor: Vendor, value: str) -> bool:
    """Whether the line's weighed-item label reads as ``value`` at this vendor."""
    codes = receipt_codes.read(
        line.printed_text or line.raw_text or "", vendor.code_position, vendor.rw_layout
    )
    return (RW, value) in codes.positioned


async def known_wordings(db: AsyncSession, product_id: uuid.UUID, vendor_id: uuid.UUID) -> set[str]:
    """The wordings a person or the ladder has already tied to the product at the vendor."""
    aliases = select(ReceiptAlias.raw_text_norm).where(
        ReceiptAlias.vendor_id == vendor_id,
        ReceiptAlias.product_id == product_id,
        ReceiptAlias.disposition == "product",
    )
    lines = (
        select(PurchaseLine.raw_text_norm)
        .join(Purchase, Purchase.id == PurchaseLine.purchase_id)
        .join(VendorLocation, VendorLocation.id == Purchase.vendor_location_id)
        .where(
            VendorLocation.vendor_id == vendor_id,
            PurchaseLine.product_id == product_id,
            PurchaseLine.resolution != "unmatched",
            PurchaseLine.removed_at.is_(None),
            PurchaseLine.raw_text_norm.is_not(None),
        )
    )
    found = (await db.execute(aliases.union(lines))).scalars().all()
    return {w for w in found if w}


async def agrees(
    db: AsyncSession,
    line: PurchaseLine,
    product_id: uuid.UUID,
    vendor_id: uuid.UUID,
    known: set[str] | None = None,
) -> bool:
    """Whether a weighed code may identify the line as the product on its own.

    ``known`` is the wordings to require; without it they are looked up, and a
    product with no wording yet at the vendor takes the first one it is read on.
    """
    if known is None:
        known = await known_wordings(db, product_id, vendor_id)
        if not known:
            return True
    return wording_of(line) in known


async def waiting_known(
    db: AsyncSession, product_id: uuid.UUID, lines: Iterable[tuple[PurchaseLine, Vendor]]
) -> dict[uuid.UUID, set[str]]:
    """Per vendor, the wordings a weighed code may identify among waiting lines.

    When the product has no wording yet at a vendor, its waiting lines may take
    the code only if they all read alike: two wordings on one code is the sign
    of a counter code, so every one of them waits for a person.
    """
    rw = (
        await db.execute(
            select(ProductIdentifier.vendor_id, ProductIdentifier.value).where(
                ProductIdentifier.product_id == product_id, ProductIdentifier.scheme == RW
            )
        )
    ).all()
    codes: dict[uuid.UUID, set[str]] = {}
    for vendor_id, value in rw:
        if vendor_id is not None:
            codes.setdefault(vendor_id, set()).add(value)
    seen: dict[uuid.UUID, set[str]] = {}
    for line, vendor in lines:
        if any(prints_code(line, vendor, v) for v in codes.get(vendor.id, ())):
            seen.setdefault(vendor.id, set()).add(wording_of(line))
    out: dict[uuid.UUID, set[str]] = {}
    for vendor_id, wordings in seen.items():
        known = await known_wordings(db, product_id, vendor_id)
        out[vendor_id] = known or (wordings if len(wordings) == 1 else set())
    return out


async def shared_wordings(
    db: AsyncSession, vendor: Vendor, value: str, line: PurchaseLine
) -> list[dict[str, Any]]:
    """Other wordings that print this weighed code at the vendor, most lines first.

    Lines already tied to the line's product, and lines that read like it, are
    not counted: only a different item behind the same code is.
    """
    if line.product_id is None:
        return []
    allowed = await known_wordings(db, line.product_id, vendor.id) | {wording_of(line)}
    candidates = (
        (
            await db.execute(
                select(PurchaseLine)
                .join(Purchase, Purchase.id == PurchaseLine.purchase_id)
                .join(VendorLocation, VendorLocation.id == Purchase.vendor_location_id)
                .where(
                    VendorLocation.vendor_id == vendor.id,
                    PurchaseLine.line_kind == "item",
                    PurchaseLine.removed_at.is_(None),
                    PurchaseLine.id != line.id,
                    (PurchaseLine.raw_text.contains(value, autoescape=True))
                    | (PurchaseLine.printed_text.contains(value, autoescape=True)),
                )
            )
        )
        .scalars()
        .all()
    )
    groups: dict[str, dict[str, Any]] = {}
    for other in candidates:
        wording = wording_of(other)
        if wording in allowed or not prints_code(other, vendor, value):
            continue
        group = groups.setdefault(wording, {"text": other.raw_text or wording, "lines": 0})
        group["lines"] += 1
    return sorted(groups.values(), key=lambda g: (-g["lines"], g["text"]))


def held_suggestion(product: Product, value: str) -> dict[str, Any]:
    """The code match offered, not applied, on a line whose wording differs."""
    return {
        "kind": "code",
        "product_id": str(product.id),
        "ignore": False,
        "label": f"{product.name} (code {value}, other wording)",
        "score": "1",
    }
