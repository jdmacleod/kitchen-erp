"""One search over ingredients, products, vendors and recipes, for the search palette.

Each group reuses the ranking its own list already has: products the typeahead's
(``catalog.search_products``, with exact barcodes first), vendors the vendor
list's trigram similarity, and ingredients the typeahead's word similarity on
the ingredient name. Recipes (UI-7.17) match on title or path, the recipes list's
own filter, in title order.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.schemas.recipes import RecipeSummary
from app.schemas.search import RecipeBadge, SearchResult, SearchResults
from app.services import catalog, geo, recipes

log = get_logger(__name__)

GROUP_LIMIT = 8

_VENDOR_KINDS = {
    "chain": "Chain",
    "independent": "Independent",
    "market": "Market",
    "stand": "Stand",
}


async def _ingredients(db: AsyncSession, q: str) -> list[SearchResult]:
    matches = await catalog.search_ingredients(db, q, limit=GROUP_LIMIT)
    return [
        SearchResult(
            kind="ingredient",
            id=m.id,
            label=m.name,
            detail=f"matches {m.matched_spelling}" if m.matched_spelling else m.category,
            route=f"/catalog/ingredients/{m.id}",
            category_key=m.category_key,
        )
        for m in matches
        if m.id is not None
    ]


async def _products(db: AsyncSession, q: str) -> list[SearchResult]:
    hits = await catalog.search_products(db, q, GROUP_LIMIT)
    return [
        SearchResult(
            kind="product",
            id=h.id,
            label=h.name,
            detail=" · ".join(part for part in (h.brand, h.ingredient.name) if part),
            route=f"/catalog/products/{h.id}",
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
            route=f"/catalog/vendors/{f.vendor.id}",
        )
        for f in found
    ]


def recipe_badge(recipe: RecipeSummary) -> RecipeBadge | None:
    """The one badge a recipe row carries: the file's state before its dirtiness (UI-7.4)."""
    if recipe.status == "parse_error":
        return "parse_error"
    if recipe.status == "missing":
        return "missing"
    return "uncommitted" if recipe.dirty else None


async def _recipes(db: AsyncSession, q: str) -> list[SearchResult]:
    found = await recipes.list_recipes(db, q=q)
    return [
        SearchResult(
            kind="recipe",
            id=r.id,
            label=r.title,
            detail=r.path,
            route=f"/cook/recipes/{r.id}",
            badge=recipe_badge(r),
        )
        for r in found[:GROUP_LIMIT]
    ]


async def search(db: AsyncSession, q: str) -> SearchResults:
    """``q`` is already 1–200 characters; one that is only whitespace finds nothing."""
    q = q.strip()
    if not q:
        return SearchResults(ingredients=[], products=[], vendors=[], recipes=[])
    results = SearchResults(
        ingredients=await _ingredients(db, q.lower()),
        products=await _products(db, q),
        vendors=await _vendors(db, q),
        recipes=await _recipes(db, q),
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
                "recipes": len(results.recipes),
            },
        },
    )
    return results
