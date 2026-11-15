"""The posted prices waiting on a person (04, 2N; PR2), each beside what its page
posted before. Recording and deciding them stays in app.services.lookups."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def pending(db: AsyncSession) -> list[dict[str, Any]]:
    rows = await db.execute(
        text(
            """
            SELECT c.id, c.amount, c.qty, c.unit, c.is_promo, c.seen_at, c.created_at,
                   l.id AS listing_id, l.title, l.canonical_url,
                   p.id AS product_id, p.name AS product_name, p.brand AS product_brand,
                   v.id AS vendor_id, v.name AS vendor_name,
                   was.price AS was_amount, was.qty AS was_qty, was.unit AS was_unit
            FROM listing_price_change c
            JOIN vendor_listing l ON l.id = c.listing_id
            JOIN product p ON p.id = l.product_id
            JOIN vendor v ON v.id = l.vendor_id
            -- What the page posted before: the change is only worth deciding beside it.
            LEFT JOIN LATERAL (
                SELECT o.price, o.qty, o.unit FROM price_observation o
                WHERE o.listing_id = l.id
                  AND NOT EXISTS (SELECT 1 FROM price_observation_void x
                                  WHERE x.observation_id = o.id)
                ORDER BY o.observed_at DESC, o.created_at DESC LIMIT 1
            ) was ON true
            WHERE c.status = 'pending'
            ORDER BY c.created_at, c.id
            """
        )
    )
    return [dict(r) for r in rows.mappings()]
