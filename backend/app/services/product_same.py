""" "Already in your catalog" before a product is created (#348).

The 2P duplicate finder finds same-product pairs after the fact. Here every way
of creating a product asks first: the active products whose names share enough
trigrams with the new one, across all ingredients (two entries of one product
are often filed under different ingredients), are compared by the sameness
rules. A ``same`` one refuses the create with ``409 product_exists`` unless the
person says to create it anyway (``allow_duplicate``); another size or variant
is listed but never refuses.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.catalog import sameness
from app.core.errors import ApiError
from app.models import Product
from app.schemas.catalog import ProductCreate
from app.services.proposals import product_facts

LIMIT = 5


@dataclass(frozen=True)
class Found:
    product: Product
    kind: sameness.Kind


def _pack(qty, unit) -> dict | None:
    return {"qty": format(qty, "f"), "unit": unit} if qty is not None and unit else None


async def find_same(
    db: AsyncSession,
    name: str,
    brand: str | None = None,
    pack_qty=None,
    pack_unit: str | None = None,
) -> list[Found]:
    """Active products the new one may already be: same first, then sizes and variants."""
    text = sameness.key_text(name, brand) or name.strip().lower()
    if not text:
        return []
    rows = (
        await db.execute(
            select(Product)
            .options(selectinload(Product.identifiers))
            .where(Product.active, func.lower(Product.name).op("%")(text))
            .order_by(func.similarity(func.lower(Product.name), text).desc())
            .limit(25)
        )
    ).scalars()
    new = sameness.Facts(name=name, brand=brand, pack=_pack(pack_qty, pack_unit))
    order = {"same": 0, "other_size": 1, "variant": 2}
    found = [
        Found(p, verdict.kind)
        for p in rows
        if (verdict := sameness.compare(new, product_facts(p))).kind in order
    ]
    found.sort(key=lambda f: order[f.kind])
    return found[:LIMIT]


async def refuse_same(db: AsyncSession, payload: ProductCreate) -> None:
    """409 ``product_exists`` when the product to create is one the catalog has."""
    if payload.allow_duplicate:
        return
    found = await find_same(db, payload.name, payload.brand, payload.pack_qty, payload.pack_unit)
    same = [f for f in found if f.kind == "same"]
    if not same:
        return
    raise ApiError(
        409,
        "product_exists",
        f"{same[0].product.name} is already in your catalog.",
        {
            "product_id": str(same[0].product.id),
            "candidates": [
                {
                    "id": str(f.product.id),
                    "name": f.product.name,
                    "brand": f.product.brand,
                    "pack_qty": format(f.product.pack_qty, "f") if f.product.pack_qty else None,
                    "pack_unit": f.product.pack_unit,
                    "verdict": f.kind,
                }
                for f in found
            ],
        },
    )
