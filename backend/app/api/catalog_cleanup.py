"""Tidy the catalog (#349): suggestions, applied or skipped by a person."""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.catalog_cleanup import (
    CleanupApplyIn,
    CleanupApplyOut,
    CleanupKeysIn,
    CleanupOut,
    CleanupSuggestionOut,
)
from app.services import catalog_cleanup

router = APIRouter(tags=["catalog cleanup"])


@router.get("/catalog/cleanup", response_model=CleanupOut)
async def list_suggestions(db: DbSession, _: CurrentUser) -> CleanupOut:
    """Every fix the catalog's checks propose, not yet applied or skipped."""
    found = await catalog_cleanup.suggestions(db)
    return CleanupOut(
        items=[
            CleanupSuggestionOut(
                key=s.key,
                kind=s.kind,
                subject_id=s.subject_id,
                subject=s.subject,
                before=s.before,
                after=s.after,
                reason=s.reason,
                ready=s.ready,
                value=s.value if s.kind == "category" else "",
                choosable=s.choosable,
                confidence=s.confidence,
            )
            for s in found
        ]
    )


@router.post("/catalog/cleanup/apply", response_model=CleanupApplyOut)
async def apply_suggestions(body: CleanupApplyIn, _: CurrentUser, db: DbSession) -> CleanupApplyOut:
    """Apply the suggestions named, as they stand now, with any category chosen instead.
    A guess named with others is held back: guesses are accepted one at a time."""
    applied, gone, held = await catalog_cleanup.apply(db, body.keys, body.chosen)
    return CleanupApplyOut(applied=applied, gone=gone, held=held)


@router.post("/catalog/cleanup/skip", status_code=status.HTTP_204_NO_CONTENT)
async def skip_suggestions(body: CleanupKeysIn, user: CurrentUser, db: DbSession) -> None:
    """Skip the suggestions named: not offered again while they stay the same."""
    await catalog_cleanup.skip(db, user, body.keys)
