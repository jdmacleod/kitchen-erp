"""Kitchen supply spend (04, 2T; issue 262).

A line marked a kitchen supply has no product and emits no price; it stays part
of its purchase and its total. What it cost is counted here, the way a price is
counted: its line total less the savings attached to it, never below zero.
Deposits and tax never count.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.supplies import CATEGORIES
from app.models import Purchase, PurchaseLine


def categories() -> list[dict[str, str]]:
    """The supply categories in their fixed order."""
    return [
        {"key": key, "label": label, "examples": examples}
        for key, (label, examples) in CATEGORIES.items()
    ]


def _cost(purchase: Purchase, line: PurchaseLine) -> Decimal:
    """As ``resolution.observation_price`` counts a price: less savings, never below 0."""
    savings = sum(
        (
            abs(d.line_total)
            for d in purchase.lines
            if d.parent_line_id == line.id and d.line_kind == "discount"
        ),
        Decimal("0"),
    )
    return max(line.line_total - savings, Decimal("0"))


def supply_total(purchase: Purchase) -> Decimal | None:
    """What a purchase's supply lines come to; None when it has none."""
    lines = [ln for ln in purchase.lines if ln.resolution == "supply"]
    if not lines:
        return None
    return sum((_cost(purchase, ln) for ln in lines), Decimal("0"))


async def supply_spend(db: AsyncSession, location_id: uuid.UUID, since: datetime) -> Decimal:
    """Kitchen supply spend at a location on committed purchases since a time."""
    value = await db.scalar(
        text(
            """
            SELECT coalesce(sum(greatest(pl.line_total - coalesce((
                       SELECT sum(abs(d.line_total)) FROM purchase_line d
                       WHERE d.parent_line_id = pl.id AND d.line_kind = 'discount'
                         AND d.removed_at IS NULL
                   ), 0), 0)), 0)
            FROM purchase_line pl
            JOIN purchase p ON p.id = pl.purchase_id
            WHERE p.vendor_location_id = CAST(:lid AS uuid) AND p.status = 'committed'
              AND p.purchased_at >= CAST(:since AS timestamptz)
              AND pl.resolution = 'supply' AND pl.removed_at IS NULL
            """
        ),
        {"lid": location_id, "since": since},
    )
    return Decimal(str(value))
