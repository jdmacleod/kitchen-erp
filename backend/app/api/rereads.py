"""Re-reading a receipt's lines (04, 2S, #272)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.rereads import RereadOut, RereadRequest
from app.services import reread

router = APIRouter(tags=["purchases"])


@router.post(
    "/purchases/{purchase_id}/rereads",
    response_model=RereadOut,
    status_code=status.HTTP_201_CREATED,
)
async def request_reread(
    purchase_id: uuid.UUID, payload: RereadRequest, user: CurrentUser, db: DbSession
) -> RereadOut:
    """Re-check the lines now, or queue reading them again; either makes a proposal."""
    return RereadOut.model_validate(await reread.request(db, user, purchase_id, payload.mode))


@router.get("/purchases/{purchase_id}/rereads/latest", response_model=RereadOut)
async def latest_reread(purchase_id: uuid.UUID, _: CurrentUser, db: DbSession) -> RereadOut:
    return RereadOut.model_validate(await reread.latest(db, purchase_id))


@router.get("/rereads/{reread_id}", response_model=RereadOut)
async def get_reread(reread_id: uuid.UUID, _: CurrentUser, db: DbSession) -> RereadOut:
    return RereadOut.model_validate(await reread.get(db, reread_id))


@router.post("/rereads/{reread_id}/apply", response_model=RereadOut)
async def apply_reread(reread_id: uuid.UUID, user: CurrentUser, db: DbSession) -> RereadOut:
    """Apply the whole proposal; a committed purchase is reopened first."""
    return RereadOut.model_validate(await reread.apply(db, user, reread_id))


@router.post("/rereads/{reread_id}/dismiss", response_model=RereadOut)
async def dismiss_reread(reread_id: uuid.UUID, user: CurrentUser, db: DbSession) -> RereadOut:
    return RereadOut.model_validate(await reread.dismiss(db, user, reread_id))
