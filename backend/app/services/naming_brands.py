"""Brands on the naming pass: the one a line's wording starts with (#390).

A receipt line often starts with the brand ("HRBR LT GRK YGRT"). A run of
leading words whose key is a known brand, from the brand dataset, its aliases
or the brands already on products, becomes the row's Brand, and the name is
built from the words after it. A word that only describes the food ("organic")
is never a brand.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.brand_names import NEVER_BRANDS, is_brand_key, name_key
from app.models import Brand, BrandAlias, Product

# The most words a brand may take, leaving at least one for the name.
_LONGEST = 4


async def by_key(db: AsyncSession) -> dict[str, str]:
    """Every known brand's display name, under its key and each alias key."""
    names = dict((await db.execute(select(Brand.id, Brand.name))).tuples().all())
    out: dict[str, str] = {name_key(name): name for name in names.values()}
    for alias_key, brand_id in (
        await db.execute(select(BrandAlias.alias_key, BrandAlias.brand_id))
    ).all():
        if brand_id in names:
            out.setdefault(alias_key, names[brand_id])
    on_products = await db.scalars(
        select(Product.brand).where(Product.brand.is_not(None)).distinct()
    )
    for brand in on_products:
        if brand:
            out.setdefault(name_key(brand), brand)
    return {k: v for k, v in out.items() if is_brand_key(k) and k not in NEVER_BRANDS}


def leading(printed: list[str], shown: list[str], brands: dict[str, str]) -> tuple[str | None, int]:
    """The brand the words start with and how many words it takes, longest first.

    Both the printed words and the words as shown (abbreviations read) are tried.
    """
    for n in range(min(_LONGEST, len(printed) - 1), 0, -1):
        for words in (shown, printed):
            if (found := brands.get(name_key(" ".join(words[:n])))) is not None:
                return found, n
    return None, 0


def complete(brands: dict[str, str], prefix: str, limit: int = 6) -> list[str]:
    """Known brand names whose key starts with what was typed, shortest first."""
    start = name_key(prefix)
    if len(start) < 2:
        return []
    found = {name for key, name in brands.items() if key.startswith(start)}
    return sorted(found, key=lambda n: (len(n), n.casefold()))[:limit]
