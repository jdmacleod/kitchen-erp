"""Queued lines that print a product's code, resolved when it gains the code (2G with 2M)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppUser, ProductIdentifier, Purchase, PurchaseLine
from app.services.purchases import get_purchase
from app.services.resolution import code_rung, commit_purchase


async def match_waiting(
    db: AsyncSession,
    user: AppUser,
    product_id: uuid.UUID,
    held: list[dict[str, Any]] | None = None,
) -> int:
    """Run the code rung again over queued lines that print one of the product's codes.

    A product often gains its barcode after the receipts that bought it were read:
    a clipped page, an edit, a remembered code. Lines already in the to-identify
    queue are not read again on their own, so this gives them the identifier rung
    a new receipt gets, and prices the ones it resolves. Aliases, similar names
    and the model are left to the queue, so saving a product never waits on them.
    A weighed code whose line reads differently is left waiting with the match as
    a suggestion, and listed in ``held``. Returns how many resolved.
    """
    codes = (
        await db.execute(
            select(ProductIdentifier.scheme, ProductIdentifier.value).where(
                ProductIdentifier.product_id == product_id
            )
        )
    ).all()
    # A barcode may be printed without its leading zeros; the ladder reads it exactly.
    cores = {value.lstrip("0") if scheme == "gtin" else value for scheme, value in codes}
    cores = {c for c in cores if len(c) >= 3}
    if not cores:
        return 0
    rows = (
        await db.execute(
            select(PurchaseLine.id, PurchaseLine.purchase_id)
            .join(Purchase, Purchase.id == PurchaseLine.purchase_id)
            .where(
                Purchase.status == "committed",
                PurchaseLine.line_kind == "item",
                PurchaseLine.resolution == "unmatched",
                PurchaseLine.removed_at.is_(None),
                or_(
                    *(PurchaseLine.raw_text.contains(c, autoescape=True) for c in cores),
                    *(PurchaseLine.printed_text.contains(c, autoescape=True) for c in cores),
                ),
            )
        )
    ).all()
    waiting: dict[uuid.UUID, set[uuid.UUID]] = {}
    for line_id, purchase_id in rows:
        waiting.setdefault(purchase_id, set()).add(line_id)
    resolved = 0
    for purchase_id, line_ids in waiting.items():
        purchase = await get_purchase(db, purchase_id, lock=True)
        changed = False
        vendor_id = purchase.vendor_location.vendor_id if purchase.vendor_location else None
        for line in purchase.lines:
            if line.id in line_ids and line.resolution == "unmatched":
                # Only the code rung: a line that merely contains the digits must
                # not wait on aliases or the model while a person saves a product.
                record: dict[str, Any] = {"rung": None}
                await code_rung(db, line, vendor_id, record)
                if record["rung"] == "identifier":
                    changed = True
                    resolved += 1
                elif "held" in record:
                    offered = record["held"]
                    rest = [
                        x for x in line.suggestions if x.get("product_id") != offered["product_id"]
                    ]
                    line.suggestions = [offered, *rest]
                    if held is not None:
                        held.append(
                            {"purchase_id": purchase_id, "line_id": line.id, "text": line.raw_text}
                        )
        await db.commit()
        if changed:
            await commit_purchase(db, user, purchase_id)
    return resolved


async def match_all_waiting(db: AsyncSession, user: AppUser) -> tuple[int, int]:
    """``match_waiting`` for every product with a code: a catch-up for codes added
    before lines matched on their own. Returns (lines resolved, products checked)."""
    products = (await db.execute(select(ProductIdentifier.product_id).distinct())).scalars().all()
    resolved = 0
    for product_id in products:
        resolved += await match_waiting(db, user, product_id)
    return resolved, len(products)
