"""``kerp products brand-families``: products saved before the brand dataset (2R).

--plan links nothing and writes a file: each product whose brand the dataset
lists, with the kind it has and the kind a store's own brand gives
(``private_label``). --apply links every product's brand and writes only the
kinds the file approves, so a kind a person chose is changed only by a person.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catalog import Product
from app.services import brands

# Kinds a house brand may replace. random_weight and loose describe how it is sold.
REPLACEABLE = ("branded", "unbranded_vendor")


async def plan(db: AsyncSession) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    products = await db.execute(
        select(Product).where(Product.brand.is_not(None), Product.active).order_by(Product.name)
    )
    for product in products.scalars():
        brand = await brands.find_brand(db, product.brand)
        if brand is None or product.kind not in (*REPLACEABLE, "private_label"):
            continue
        rows.append(
            {
                "id": str(product.id),
                "name": product.name,
                "brand": product.brand,
                "brand_key": brand.key,
                "family": brand.family.name,
                "current": product.kind,
                "proposed": "private_label",
            }
        )
    return rows


async def apply(db: AsyncSession, decisions: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"linked": 0, "changed": 0, "unchanged": 0, "missing": 0}
    for product in (await db.execute(select(Product).where(Product.brand.is_not(None)))).scalars():
        counts["linked"] += await brands.link_product(db, product)
    for decision in decisions:
        product = await db.get(Product, uuid.UUID(str(decision["id"])))
        if product is None:
            counts["missing"] += 1
            continue
        kind = decision.get("proposed")
        if kind != "private_label" or product.kind == kind or product.kind not in REPLACEABLE:
            counts["unchanged"] += 1
            continue
        product.kind = kind
        counts["changed"] += 1
    await db.commit()
    return counts
