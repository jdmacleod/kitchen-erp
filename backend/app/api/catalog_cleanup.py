"""Tidy the catalog (#349): suggestions, applied or skipped by a person."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.catalog_cleanup import (
    CleanupApplyIn,
    CleanupApplyOut,
    CleanupKeysIn,
    CleanupOut,
    CleanupResultOut,
    CleanupSuggestionOut,
)
from app.services import catalog_cleanup
from app.services.cleanup_suggestion import as_out

router = APIRouter(tags=["catalog cleanup"])


@router.get("/catalog/cleanup", response_model=CleanupOut)
async def list_suggestions(db: DbSession, _: CurrentUser) -> CleanupOut:
    """Every fix the catalog's checks propose, not yet applied or skipped."""
    found = await catalog_cleanup.suggestions(db)
    return CleanupOut(items=[CleanupSuggestionOut(**as_out(s)) for s in found])


@router.post("/catalog/cleanup/apply", response_model=CleanupApplyOut)
async def apply_suggestions(body: CleanupApplyIn, _: CurrentUser, db: DbSession) -> CleanupApplyOut:
    """Apply the suggestions named, as they stand now, with any category chosen instead.
    A guess named with others is held back: guesses are accepted one at a time. One
    key named alone that is gone or skipped is 409 ``cleanup_gone``; a move or unit
    switch whose fingerprint differs from the one sent is 409 ``cleanup_changed``,
    with the suggestion as it is now."""
    done = await catalog_cleanup.apply(db, body.keys, body.chosen, body.fingerprints)
    return CleanupApplyOut(
        applied=done.applied,
        gone=done.gone,
        held=done.held,
        results=[CleanupResultOut(**r) for r in done.results],
    )


@router.post("/catalog/cleanup/skip", status_code=status.HTTP_204_NO_CONTENT)
async def skip_suggestions(body: CleanupKeysIn, user: CurrentUser, db: DbSession) -> None:
    """Skip the suggestions named: not offered again while they stay the same."""
    await catalog_cleanup.skip(db, user, body.keys)
