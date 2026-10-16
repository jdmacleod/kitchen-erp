"""Products a naming row may already be, shown before Create (04, 2I; #386).

A row whose wording names an ingredient that already has products offers up to
three of them, closest to the suggested name first, so the person can identify
the lines as one instead of creating a duplicate and being refused. Nothing is
chosen for them.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import sameness
from app.models import Product

LIMIT = 3
_ORDER = {"same": 0, "other_size": 1, "variant": 2, "similar": 3}


async def by_ingredient(
    db: AsyncSession, ingredient_ids: set[uuid.UUID]
) -> dict[uuid.UUID, list[Product]]:
    """Every active product of these ingredients, in one query (#183). The query runs
    even for none, so the naming list costs the same however many rows match."""
    rows = await db.scalars(
        select(Product)
        .where(Product.active, Product.ingredient_id.in_(ingredient_ids))
        .order_by(Product.created_at, Product.id)
    )
    out: dict[uuid.UUID, list[Product]] = defaultdict(list)
    for product in rows:
        out[product.ingredient_id].append(product)
    return out


def rank(name: str, products: list[Product]) -> list[Product]:
    """The products closest to ``name``: by the sameness verdict, then by spelling."""
    new = sameness.Facts(name=name)
    key = sameness.key_text(name) or name.lower()

    def order(p: Product) -> tuple[int, float]:
        verdict = sameness.compare(new, sameness.Facts(name=p.name, brand=p.brand))
        close = SequenceMatcher(None, key, sameness.key_text(p.name, p.brand) or p.name.lower())
        return _ORDER[verdict.kind], -close.ratio()

    return sorted(products, key=order)[:LIMIT]


def brief(product: Product) -> dict:
    return {
        "id": product.id,
        "name": product.name,
        "brand": product.brand,
        "pack_qty": product.pack_qty,
        "pack_unit": product.pack_unit,
    }
