"""The search palette's results (docs/spec/09-information-architecture.md, Search)."""

from __future__ import annotations

import uuid
from typing import Literal

from app.catalog.categories import CategoryKey
from app.schemas.base import ApiModel

SearchKind = Literal["ingredient", "product", "vendor", "recipe"]
# A recipe row's badge (UI-7.4): a dirty file, a file that cannot be read, a vanished file.
RecipeBadge = Literal["uncommitted", "parse_error", "missing"]


class SearchResult(ApiModel):
    kind: SearchKind
    id: uuid.UUID
    label: str
    detail: str | None
    route: str
    # The ingredient's category, or a product's ingredient's; vendors have none.
    category_key: CategoryKey | None = None
    # Recipes only: the badge the row carries, in the same words as the list (UI-7.17).
    badge: RecipeBadge | None = None


class SearchResults(ApiModel):
    """Grouped by type. The palette shows ingredients first, recipes after vendors
    (UI-7.17), and hides empty groups."""

    ingredients: list[SearchResult]
    products: list[SearchResult]
    vendors: list[SearchResult]
    recipes: list[SearchResult] = []
