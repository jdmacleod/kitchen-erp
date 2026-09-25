"""The search palette (docs/spec/09-information-architecture.md, Search)."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.schemas.search import SearchResults
from app.services import search as search_service

router = APIRouter(tags=["search"])


@router.get("/search", response_model=SearchResults)
async def search(
    _: CurrentUser,
    db: DbSession,
    q: str = Query(
        min_length=1, max_length=200, description="ingredient, product, vendor or barcode"
    ),
) -> SearchResults:
    return await search_service.search(db, q)
