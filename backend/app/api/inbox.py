"""The unified inbox (docs/spec/09-information-architecture.md)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.schemas.inbox import InboxOut
from app.services import inbox as inbox_service

router = APIRouter(tags=["inbox"])


@router.get("/inbox", response_model=InboxOut)
async def inbox(_: CurrentUser, db: DbSession) -> InboxOut:
    return await inbox_service.inbox(db)
