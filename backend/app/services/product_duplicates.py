"""Possible duplicate products (2P, 03): pairs the sameness rules call the same.

Trigram similarity over names, through the product name index, proposes pairs of
active products; the sameness rules keep only those they call the same product, so a
different size or a different variant is never offered. A pair a person marked "Not
the same" is remembered in ``product_distinct_pair`` and never offered again. Nothing
is merged here: "Merge…" is the product merge (#179).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.catalog import sameness
from app.core.errors import ApiError
from app.models import AppUser, Product, ProductDistinctPair
from app.services.proposals import product_facts

# Pairs whose names share enough trigrams to be worth comparing (pg_trgm's ``%``,
# at its default threshold of 0.3, which the name index serves).
_PAIRS_SQL = text(
    """
    SELECT a.id AS a, b.id AS b
    FROM product a
    JOIN product b ON a.id < b.id AND lower(a.name) % lower(b.name)
    WHERE a.active AND b.active
      AND NOT EXISTS (
          SELECT 1 FROM product_distinct_pair d
          WHERE d.product_a = a.id AND d.product_b = b.id
      )
    """
)


@dataclass
class DuplicatePair:
    a: Product
    b: Product
    reasons: tuple[str, ...]


async def find_duplicates(db: AsyncSession) -> list[DuplicatePair]:
    """Every pair of active products the sameness rules call the same, by name."""
    pairs = [(r.a, r.b) for r in await db.execute(_PAIRS_SQL)]
    if not pairs:
        return []
    ids = {i for pair in pairs for i in pair}
    products = {
        p.id: p
        for p in (
            await db.execute(
                select(Product)
                .options(selectinload(Product.ingredient), selectinload(Product.identifiers))
                .where(Product.id.in_(ids))
            )
        ).scalars()
    }
    facts = {pid: product_facts(p) for pid, p in products.items()}
    out = []
    for a, b in pairs:
        verdict = sameness.compare(facts[a], facts[b])
        if verdict.kind == "same":
            out.append(DuplicatePair(products[a], products[b], verdict.reasons))
    out.sort(key=lambda d: (d.a.name.lower(), d.b.name.lower()))
    return out


async def mark_distinct(
    db: AsyncSession, user: AppUser, first: uuid.UUID, second: uuid.UUID
) -> None:
    """Remember that two products are not the same; either order names the same pair."""
    if first == second:
        raise ApiError(422, "same_product", "Choose two different products.")
    found = (await db.execute(select(Product.id).where(Product.id.in_([first, second])))).all()
    if len(found) != 2:
        raise ApiError(404, "not_found", "No such product.")
    a, b = sorted((first, second))
    await db.execute(
        insert(ProductDistinctPair)
        .values(product_a=a, product_b=b, decided_by=user.id)
        .on_conflict_do_nothing()
    )
    await db.commit()
