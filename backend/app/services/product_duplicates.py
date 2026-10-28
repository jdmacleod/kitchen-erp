"""Possible duplicate products (2P, 03): pairs the sameness rules call the same.

Pairs of active products whose names could be the same by their words (found through an
index of words, not by comparing every pair) and share enough trigrams are compared; the
sameness rules keep only those they call the same product, so a different size or a
different variant is never offered. Produce bought by weight has its size grade read as
filler there, and an organic product is never the same as a conventional one. A pair a
person marked "Not the same" is remembered in ``product_distinct_pair`` and never offered
again. Nothing is merged here: "Merge…" is the product merge (#179).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.catalog import sameness
from app.core.errors import ApiError
from app.models import AppUser, Product, ProductDistinctPair
from app.services.proposals import product_facts

# Of the pairs whose words could make them the same, those whose names share enough
# trigrams to be worth comparing (pg_trgm's ``%``, at its default threshold of 0.3) and
# that nobody has marked "Not the same".
_PAIRS_SQL = text(
    """
    SELECT a.id AS a, b.id AS b
    FROM unnest(CAST(:a AS uuid[]), CAST(:b AS uuid[])) AS pair (a_id, b_id)
    JOIN product a ON a.id = pair.a_id
    JOIN product b ON b.id = pair.b_id
    WHERE lower(a.name) % lower(b.name)
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


# Active produce bought by weight: a random-weight product, or one with a price for a
# weight. Its size grade is filler to the sameness rules.
_PRODUCE_BY_WEIGHT_SQL = text(
    """
    SELECT p.id
    FROM product p
    JOIN ingredient i ON i.id = p.ingredient_id
    WHERE p.active AND lower(i.category) = 'produce'
      AND (p.kind = 'random_weight' OR EXISTS (
          SELECT 1 FROM price_observation o JOIN unit u ON u.code = o.unit
          WHERE o.product_id = p.id AND u.dimension = 'mass'
      ))
    """
)


def _word_pairs(facts: dict[uuid.UUID, sameness.Facts]) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Pairs (lower id first) whose names pass the sameness words test, found through an
    index of the words that answer for others, so a catalog isn't compared pair by pair."""
    sets = {pid: sameness.word_sets(f) for pid, f in facts.items()}
    covering: dict[str, set[uuid.UUID]] = defaultdict(set)
    for pid, words in sets.items():
        for word in words.cover:
            covering[word].add(pid)
    found: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for pid, words in sets.items():
        # A product that could agree covers every needed word, so the rarest one is enough.
        others = min((covering[w] for w in words.need), key=len) if words.need else sets.keys()
        for other in others:
            if other != pid and sameness.words_agree(words, sets[other]):
                found.add((min(pid, other), max(pid, other)))
    return sorted(found)


async def find_duplicates(db: AsyncSession) -> list[DuplicatePair]:
    """Every pair of active products the sameness rules call the same, by name."""
    by_weight = set((await db.execute(_PRODUCE_BY_WEIGHT_SQL)).scalars())
    rows = await db.execute(
        select(Product.id, Product.name, Product.brand, Product.attributes).where(Product.active)
    )
    candidates = _word_pairs(
        {
            pid: sameness.Facts(
                name=name,
                brand=brand,
                produce_by_weight=pid in by_weight,
                **sameness.attribute_facts(attributes),
            )
            for pid, name, brand, attributes in rows.tuples()
        }
    )
    if not candidates:
        return []
    rows = await db.execute(
        _PAIRS_SQL, {"a": [a for a, _ in candidates], "b": [b for _, b in candidates]}
    )
    pairs = [(r.a, r.b) for r in rows]
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
    facts = {
        pid: product_facts(p, produce_by_weight=pid in by_weight) for pid, p in products.items()
    }
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
