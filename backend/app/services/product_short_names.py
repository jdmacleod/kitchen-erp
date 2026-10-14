"""A product's short name, with what the household knows (#347).

``product_names.short_name`` is pure; this gives it the household's brands (the
brand dataset, its aliases and the brands on products) and the standard list's
proper nouns, for a proposal's suggested name and the Add product hint.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import proposals as merging
from app.catalog.conform import proper_noun_words
from app.catalog.product_names import short_name
from app.services.ingredient_names import brand_keys


async def suggest(db: AsyncSession, title: str, brand: str | None, *, has_pack: bool) -> str:
    return short_name(
        title,
        brand,
        has_pack=has_pack,
        brand_keys=await brand_keys(db),
        proper_nouns=proper_noun_words(),
    )


async def for_proposal(db: AsyncSession, fields: dict[str, Any]) -> str | None:
    """The short name for a proposal's title, or None when it has none or it is short."""
    title = merging.value(fields, "title")
    if not isinstance(title, str) or not title.strip():
        return None
    brand = merging.value(fields, "brand")
    has_pack = merging.value(fields, "pack") is not None
    found = await suggest(db, title, brand if isinstance(brand, str) else None, has_pack=has_pack)
    return found if found != title else None
