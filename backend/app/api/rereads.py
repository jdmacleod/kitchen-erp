"""Re-reading a receipt's lines (04, 2S, #272)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.rereads import OlderRulesOut, RereadOut, RereadRequest
from app.services import reread, reread_older

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


# Declared before /rereads/{reread_id}, which would otherwise take the path.
@router.get("/rereads/older-rules", response_model=OlderRulesOut)
async def older_rules(_: CurrentUser, db: DbSession) -> OlderRulesOut:
    """How many drafts were read with older line rules, and the re-checks ready (issue 382)."""
    return OlderRulesOut.model_validate(await reread_older.overview(db))


@router.post("/rereads/older-rules/recheck", response_model=OlderRulesOut)
async def recheck_older(user: CurrentUser, db: DbSession) -> OlderRulesOut:
    """Re-check each of them with today's rules: no model, nothing applied."""
    return OlderRulesOut.model_validate(await reread_older.recheck(db, user))


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
