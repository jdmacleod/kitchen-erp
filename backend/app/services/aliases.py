"""Receipt aliases: a vendor's wording, confirmed as one product or as ignored (2D).

Every human confirmation writes or counts up an alias, and a confirmed product
also teaches what the vendor's printed words stand for (#388).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models import ReceiptAlias
from app.services import line_words


async def upsert_alias(
    db: AsyncSession, vendor_id: uuid.UUID, raw_text_norm: str, product_id: uuid.UUID | None
) -> ReceiptAlias:
    """Every human confirmation writes or updates an alias. Re-pointing resets the count."""
    if not raw_text_norm:
        raise ApiError(422, "empty_alias", "The line has no text to learn from.")
    alias = (
        await db.execute(
            select(ReceiptAlias).where(
                ReceiptAlias.vendor_id == vendor_id, ReceiptAlias.raw_text_norm == raw_text_norm
            )
        )
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    disposition = "product" if product_id is not None else "ignore"
    if alias is None:
        alias = ReceiptAlias(
            vendor_id=vendor_id,
            raw_text_norm=raw_text_norm,
            disposition=disposition,
            product_id=product_id,
            confirmed_count=1,
            last_seen_at=now,
        )
        db.add(alias)
    elif alias.disposition == disposition and alias.product_id == product_id:
        alias.confirmed_count += 1
        alias.last_seen_at = now
    else:
        alias.disposition = disposition
        alias.product_id = product_id
        alias.confirmed_count = 1
        alias.last_seen_at = now
    await db.flush()
    if product_id is not None:
        await line_words.learn_from_product(db, vendor_id, raw_text_norm, product_id)
    return alias
