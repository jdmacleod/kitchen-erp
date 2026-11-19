"""Tidy the catalog (#349): suggestions, applied or skipped by a person."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.catalog_cleanup import (
    CleanupApplyIn,
    CleanupApplyOut,
    CleanupKeysIn,
    CleanupOut,
    CleanupResultOut,
    CleanupSuggestionOut,
    UnitSwitchIn,
    UnitSwitchOut,
)
from app.services import catalog_cleanup, unit_switch
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


@router.get("/ingredients/{ingredient_id}/unit-switch", response_model=UnitSwitchOut)
async def get_unit_switch(ingredient_id: uuid.UUID, _: CurrentUser, db: DbSession) -> UnitSwitchOut:
    """What switching the ingredient to its standard entry's unit would do (#527)."""
    found = await unit_switch.for_ingredient(db, ingredient_id)
    return UnitSwitchOut(suggestion=CleanupSuggestionOut(**as_out(found[0])) if found else None)


@router.post("/ingredients/{ingredient_id}/unit-switch", response_model=CleanupResultOut)
async def switch_unit(
    ingredient_id: uuid.UUID, body: UnitSwitchIn, _: CurrentUser, db: DbSession
) -> CleanupResultOut:
    """Switch the ingredient to its standard entry's unit, as Tidy the catalog's accept
    does, in one transaction. 409 cleanup_gone: no switch to make; 409
    cleanup_changed: the consequences differ from the fingerprint sent; 500
    unit_switch_failed: it failed, and nothing changed."""
    done = await catalog_cleanup.switch_unit(db, ingredient_id, body.fingerprint)
    return CleanupResultOut(**done.results[0])
