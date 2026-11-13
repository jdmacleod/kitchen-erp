"""Bridges waiting for a person's check (issue 356). Writes use the catalog routes."""

from __future__ import annotations

import uuid
from dataclasses import asdict

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.schemas.bridges import ConfirmedCount, UnconfirmedList, UnconfirmedValueOut
from app.services import confirm_bridges, unconfirmed_bridges

router = APIRouter(tags=["catalog"])


@router.get("/bridges/unconfirmed", response_model=UnconfirmedList)
async def unconfirmed(db: DbSession, _: CurrentUser) -> UnconfirmedList:
    """Every unconfirmed density, measure and product density, implausible ones first."""
    found = await unconfirmed_bridges.unconfirmed(db)
    return UnconfirmedList(items=[UnconfirmedValueOut(**asdict(v)) for v in found])


@router.post("/ingredients/{ingredient_id}/bridges/confirm", response_model=ConfirmedCount)
async def confirm_ingredient(
    ingredient_id: uuid.UUID, db: DbSession, _: CurrentUser
) -> ConfirmedCount:
    """Confirm an ingredient's unconfirmed density and measures, implausible ones aside."""
    return ConfirmedCount(confirmed=await confirm_bridges.confirm_ingredient(db, ingredient_id))
