"""A typed ingredient name, conformed before it is saved (#345).

``catalog.conform`` is pure; this adds the brands the household knows, from the
brand dataset (names and aliases) and the brands on its products, so "Harbor
Light pepper relish" is offered as "pepper relish". The UI shows the result as
"Will be saved as …" with a way to keep the name as typed.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.brand_names import name_key
from app.catalog.conform import Conformed, conform
from app.models.brands import Brand, BrandAlias
from app.models.catalog import Product


async def brand_keys(db: AsyncSession) -> set[str]:
    names = (await db.execute(select(Brand.name))).scalars().all()
    aliases = (await db.execute(select(BrandAlias.alias_key))).scalars().all()
    on_products = (
        (await db.execute(select(Product.brand).where(Product.brand.is_not(None)).distinct()))
        .scalars()
        .all()
    )
    keys = {name_key(n) for n in [*names, *on_products]} | set(aliases)
    return {k for k in keys if k}


async def preview(db: AsyncSession, name: str) -> Conformed:
    return conform(name, brand_keys=await brand_keys(db))
