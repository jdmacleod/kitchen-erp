"""A vendor location's circular (18, 2U-4; UI-9.5): set it up, turn it on or off,
and ask for a read now."""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.schemas.flyers import CircularIn, CircularOut, CircularPatch
from app.services import flyer_sources

router = APIRouter(prefix="/vendor-locations/{location_id}/circular", tags=["flyers"])


@router.get("", response_model=CircularOut)
async def get_circular(location_id: uuid.UUID, _: CurrentUser, db: DbSession) -> CircularOut:
    """The location's circular, its last four runs, and whether a read is waiting."""
    return CircularOut.model_validate(await flyer_sources.circular(db, location_id))


@router.put("", response_model=CircularOut)
async def put_circular(
    location_id: uuid.UUID, payload: CircularIn, _: CurrentUser, db: DbSession
) -> CircularOut:
    """Set up the circular, or replace what the helper is told about it."""
    values = payload.model_dump()
    return CircularOut.model_validate(await flyer_sources.put_circular(db, location_id, values))


@router.patch("", response_model=CircularOut)
async def patch_circular(
    location_id: uuid.UUID, payload: CircularPatch, _: CurrentUser, db: DbSession
) -> CircularOut:
    """Turn it on or off, ask before using its offers, or move its weekday."""
    values = payload.model_dump(exclude_none=True)
    return CircularOut.model_validate(await flyer_sources.patch_circular(db, location_id, values))


@router.post("/check", response_model=CircularOut)
async def check_now(location_id: uuid.UUID, _: CurrentUser, db: DbSession) -> CircularOut:
    """Ask the helper to read this week's circular now; lifts a pause."""
    return CircularOut.model_validate(await flyer_sources.check_now(db, location_id))
