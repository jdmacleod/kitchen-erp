"""Weighed-item codes that may name a counter, not an item (04, 2K; #448).

A weighed-item label's item code is read with the vendor's layout. At some
counters several items print the same code, and the digit that tells them apart
falls outside the layout. So a weighed code identifies a line on its own only
when the line's wording is already known for that product at that vendor, or,
with none known yet, when no other wording there prints the code; any other
line keeps the match as a suggestion for a person to confirm. GTINs, PLUs and
vendor SKUs name one item and are not checked.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import receipt_codes
from app.models import Purchase, PurchaseLine, ReceiptAlias
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
    """The wordings a person, or an alias a person confirmed, has tied to the product here.

    Lines the code itself identified do not count: a counter code that once
    identified another item's line must not vouch for that line's wording.
    """
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
            PurchaseLine.resolution.not_in(("unmatched", "identifier", "barcode")),
            PurchaseLine.removed_at.is_(None),
            PurchaseLine.raw_text_norm.is_not(None),
        )
    )
    found = (await db.execute(aliases.union(lines))).scalars().all()
    return {w for w in found if w}


async def _other_wordings(
    db: AsyncSession, vendor: Vendor, value: str, line: PurchaseLine, allowed: set[str]
) -> dict[str, int]:
    """Wordings outside ``allowed`` that print this weighed code at the vendor, with line counts."""
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
    counts: dict[str, int] = {}
    for other in candidates:
        wording = wording_of(other)
        if wording not in allowed and prints_code(other, vendor, value):
            counts[wording] = counts.get(wording, 0) + 1
    return counts


async def agrees(
    db: AsyncSession, line: PurchaseLine, product_id: uuid.UUID, vendor: Vendor, value: str
) -> bool:
    """Whether the weighed code ``value`` may identify the line as the product on its own.

    Yes when the line's wording is known for the product at the vendor. With no
    wording known yet, yes only when no other wording at the vendor prints the
    code: two wordings on one code is the sign of a counter code, so neither takes it.
    """
    known = await known_wordings(db, product_id, vendor.id)
    if known:
        return wording_of(line) in known
    return not await _other_wordings(db, vendor, value, line, {wording_of(line)})


async def shared_wordings(
    db: AsyncSession, vendor: Vendor, value: str, line: PurchaseLine
) -> list[dict[str, Any]]:
    """Other wordings that print this weighed code at the vendor, most lines first.

    Wordings already tied to the line's product, and the line's own, are not
    counted: only a different item behind the same code is.
    """
    if line.product_id is None:
        return []
    allowed = await known_wordings(db, line.product_id, vendor.id) | {wording_of(line)}
    counts = await _other_wordings(db, vendor, value, line, allowed)
    groups = [{"text": text, "lines": n} for text, n in counts.items()]
    return sorted(groups, key=lambda g: (-g["lines"], g["text"]))


def code_suggestion(
    product_id: uuid.UUID, name: str, value: str, *, held: bool = False
) -> dict[str, Any]:
    """A code match offered for a person to tap; ``held`` when the line reads differently."""
    return {
        "kind": "code",
        "product_id": str(product_id),
        "ignore": False,
        "label": f"{name} (code {value}, other wording)" if held else f"{name} (code {value})",
        "score": "1",
    }
