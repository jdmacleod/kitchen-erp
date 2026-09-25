"""The search palette's results (docs/spec/09-information-architecture.md, Search)."""

from __future__ import annotations

import uuid
from typing import Literal

from app.catalog.categories import CategoryKey
from app.schemas.base import ApiModel

SearchKind = Literal["ingredient", "product", "vendor"]


class SearchResult(ApiModel):
    kind: SearchKind
    id: uuid.UUID
    label: str
    detail: str | None
    route: str
    # The ingredient's category, or a product's ingredient's; vendors have none.
    category_key: CategoryKey | None = None


class SearchResults(ApiModel):
    """Grouped by type. The palette shows ingredients first and hides empty groups."""

    ingredients: list[SearchResult]
    products: list[SearchResult]
    vendors: list[SearchResult]
