"""Bridges waiting for a person's check (issue 356). Writes use the catalog routes."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.schemas.bridges import UnconfirmedList, UnconfirmedValueOut
from app.services import unconfirmed_bridges

router = APIRouter(tags=["catalog"])


@router.get("/bridges/unconfirmed", response_model=UnconfirmedList)
async def unconfirmed(db: DbSession, _: CurrentUser) -> UnconfirmedList:
    """Every unconfirmed density, measure and product density, implausible ones first."""
    found = await unconfirmed_bridges.unconfirmed(db)
    return UnconfirmedList(items=[UnconfirmedValueOut(**asdict(v)) for v in found])
