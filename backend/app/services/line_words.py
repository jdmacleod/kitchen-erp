"""Learning what a vendor's printed words stand for (#388).

Every time a person confirms a line's product, each printed word that abbreviates
exactly one word of the product's name (``catalog.line_words.align``) counts once
for that vendor and once for any vendor. A vendor's own expansions win; the
any-vendor row is the fallback for a word that vendor hasn't printed yet.

Expansions are suggestions for the naming pass. Nothing here rewrites a line's
printed or normalized text.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.line_words import align
from app.models import LineWord, Product, ReceiptAlias

_KEY = ["vendor_id", "word", "expansion"]


@dataclass(frozen=True)
class Expansion:
    expansion: str
    count: int
    # True when learned at this vendor, False for the any-vendor fallback.
    own: bool


async def _add(db: AsyncSession, vendor_id: uuid.UUID | None, word: str, expansion: str) -> None:
    stmt = insert(LineWord).values(vendor_id=vendor_id, word=word, expansion=expansion)
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=_KEY,
            set_={"confirmed_count": LineWord.confirmed_count + 1, "updated_at": func.now()},
        )
    )


async def learn(
    db: AsyncSession, vendor_id: uuid.UUID, raw_text_norm: str, product_name: str
) -> list[tuple[str, str]]:
    """Count each word this naming teaches, at the vendor and at any vendor."""
    pairs = align(raw_text_norm, product_name)
    for word, expansion in pairs:
        await _add(db, vendor_id, word, expansion)
        await _add(db, None, word, expansion)
    return pairs


async def learn_from_product(
    db: AsyncSession, vendor_id: uuid.UUID, raw_text_norm: str, product_id: uuid.UUID
) -> None:
    product = await db.get(Product, product_id)
    if product is not None:
        await learn(db, vendor_id, raw_text_norm, product.name)


async def forget(db: AsyncSession, vendor_id: uuid.UUID, word: str, expansion: str) -> None:
    """A person undid this expansion: count it once less, here and at any vendor."""
    await db.execute(
        update(LineWord)
        .where(
            or_(LineWord.vendor_id == vendor_id, LineWord.vendor_id.is_(None)),
            LineWord.word == word.lower(),
            LineWord.expansion == expansion.lower(),
            LineWord.confirmed_count > 0,
        )
        .values(confirmed_count=LineWord.confirmed_count - 1, updated_at=func.now())
    )


async def expansions(db: AsyncSession, vendor_id: uuid.UUID) -> dict[str, list[Expansion]]:
    """Every word with an expansion still offered at this vendor, most confirmed first.

    A word the vendor itself taught lists only its own expansions; any other word
    lists the any-vendor ones.
    """
    rows = (
        await db.execute(
            select(LineWord).where(
                or_(LineWord.vendor_id == vendor_id, LineWord.vendor_id.is_(None)),
                LineWord.confirmed_count > 0,
            )
        )
    ).scalars()
    own: dict[str, list[Expansion]] = {}
    anywhere: dict[str, list[Expansion]] = {}
    for row in rows:
        target = own if row.vendor_id is not None else anywhere
        target.setdefault(row.word, []).append(
            Expansion(row.expansion, row.confirmed_count, row.vendor_id is not None)
        )
    out = {word: own.get(word) or found for word, found in anywhere.items()} | own
    return {
        word: sorted(found, key=lambda e: (-e.count, e.expansion)) for word, found in out.items()
    }


async def backfill(db: AsyncSession) -> int:
    """Learn from every alias that names a product, adding only words not yet known.

    One alias counts once. A pair already in the table is left alone, so running
    this again changes nothing. Returns the number of rows added.
    """
    rows = await db.execute(
        select(ReceiptAlias.vendor_id, ReceiptAlias.raw_text_norm, Product.name)
        .join(Product, Product.id == ReceiptAlias.product_id)
        .where(ReceiptAlias.product_id.is_not(None))
    )
    counts: Counter[tuple[uuid.UUID | None, str, str]] = Counter()
    for vendor_id, raw_text_norm, name in rows:
        for word, expansion in align(raw_text_norm, name):
            counts[(vendor_id, word, expansion)] += 1
            counts[(None, word, expansion)] += 1
    added = 0
    for (vendor_id, word, expansion), count in counts.items():
        result = await db.execute(
            insert(LineWord)
            .values(vendor_id=vendor_id, word=word, expansion=expansion, confirmed_count=count)
            .on_conflict_do_nothing(index_elements=_KEY)
            .returning(LineWord.id)
        )
        added += len(result.all())
    return added
