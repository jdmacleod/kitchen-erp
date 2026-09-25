"""One search over ingredients, products and vendors, for the search palette.

Each group reuses the ranking its own list already has: products the typeahead's
(``catalog.search_products``, with exact barcodes first), vendors the vendor
list's trigram similarity, and ingredients the typeahead's word similarity on
the ingredient name.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import categories
from app.core.logging import get_logger
from app.schemas.search import SearchResult, SearchResults
from app.services import catalog, geo
from app.services.catalog import like_escape

log = get_logger(__name__)

GROUP_LIMIT = 8

_VENDOR_KINDS = {
    "chain": "Chain",
    "independent": "Independent",
    "market": "Market",
    "stand": "Stand",
}

_INGREDIENT_SQL = text(
    """
    SELECT i.id, i.name, i.category
    FROM ingredient i
    WHERE i.active AND (lower(i.name) LIKE :like OR :ql <% lower(i.name))
    ORDER BY word_similarity(:ql, lower(i.name)) DESC,
             (lower(i.name) LIKE :prefix) DESC,
             i.name
    LIMIT :limit
    """
)


async def _ingredients(db: AsyncSession, ql: str) -> list[SearchResult]:
    rows = await db.execute(
        _INGREDIENT_SQL,
        {
            "ql": ql,
            "like": f"%{like_escape(ql)}%",
            "prefix": f"{like_escape(ql)}%",
            "limit": GROUP_LIMIT,
        },
    )
    return [
        SearchResult(
            kind="ingredient",
            id=r["id"],
            label=r["name"],
            detail=r["category"],
            route=f"/ingredients/{r['id']}",
            category_key=categories.key(r["category"]),
        )
        for r in rows.mappings()
    ]


async def _products(db: AsyncSession, q: str) -> list[SearchResult]:
    hits = await catalog.search_products(db, q, GROUP_LIMIT)
    return [
        SearchResult(
            kind="product",
            id=h.id,
            label=h.name,
            detail=" · ".join(part for part in (h.brand, h.ingredient.name) if part),
            route=f"/products/{h.id}",
            category_key=h.ingredient.category_key,
        )
        for h in hits
    ]


async def _vendors(db: AsyncSession, q: str) -> list[SearchResult]:
    found = await geo.list_vendors(db, q=q, limit=GROUP_LIMIT)
    return [
        SearchResult(
            kind="vendor",
            id=f.vendor.id,
            label=f.vendor.name,
            detail=_VENDOR_KINDS.get(f.vendor.kind),
            route=f"/vendors/{f.vendor.id}",
        )
        for f in found
    ]


async def search(db: AsyncSession, q: str) -> SearchResults:
    """``q`` is already 1–200 characters; one that is only whitespace finds nothing."""
    q = q.strip()
    if not q:
        return SearchResults(ingredients=[], products=[], vendors=[])
    results = SearchResults(
        ingredients=await _ingredients(db, q.lower()),
        products=await _products(db, q),
        vendors=await _vendors(db, q),
    )
    # Counts and length only: the query may be receipt-derived text.
    log.info(
        "search",
        extra={
            "q_length": len(q),
            "counts": {
                "ingredients": len(results.ingredients),
                "products": len(results.products),
                "vendors": len(results.vendors),
            },
        },
    )
    return results
