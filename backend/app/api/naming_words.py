"""Word completions while a person types a Name on the naming pass (04, 2I; #385)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.schemas.naming_words import BrandCompletions, WordCompletion, WordCompletions
from app.services import naming_brands, naming_words

router = APIRouter(tags=["purchases"])


@router.get("/to-identify/naming/words", response_model=WordCompletions)
async def complete_word(
    db: DbSession,
    _: CurrentUser,
    vendor_id: uuid.UUID,
    prefix: str = Query(min_length=1, max_length=40),
) -> WordCompletions:
    """Words completing the one being typed: this vendor's learned ones, then the vocabulary's."""
    found = await naming_words.complete(db, vendor_id, prefix)
    return WordCompletions(items=[WordCompletion(**w) for w in found])


@router.get("/to-identify/naming/brands", response_model=BrandCompletions)
async def complete_brand(
    db: DbSession,
    _: CurrentUser,
    prefix: str = Query(min_length=1, max_length=80),
) -> BrandCompletions:
    """Known brands starting with what was typed in a row's Brand (#390)."""
    return BrandCompletions(items=naming_brands.complete(await naming_brands.by_key(db), prefix))
