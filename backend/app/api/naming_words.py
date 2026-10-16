"""Word completions while a person types a Name on the naming pass (04, 2I; #385)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.schemas.naming_words import WordCompletion, WordCompletions
from app.services import naming_words

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
