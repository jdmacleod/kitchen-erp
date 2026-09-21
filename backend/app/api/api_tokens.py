from __future__ import annotations

import uuid

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.api.deps import CurrentUser, DbSession, Idempotency
from app.schemas.identity import ApiTokenCreate, ApiTokenCreated, ApiTokenList, ApiTokenOut
from app.services import identity

router = APIRouter(prefix="/api-tokens", tags=["api-tokens"])


@router.get("", response_model=ApiTokenList)
async def list_tokens(user: CurrentUser, db: DbSession) -> ApiTokenList:
    return ApiTokenList(
        items=[ApiTokenOut.model_validate(t) for t in await identity.list_api_tokens(db, user)]
    )


@router.post("", response_model=ApiTokenCreated, status_code=status.HTTP_201_CREATED)
async def create_token(
    payload: ApiTokenCreate, user: CurrentUser, db: DbSession, guard: Idempotency
) -> JSONResponse:
    if guard.replay is not None:
        return guard.replay
    token, plaintext = await identity.create_api_token(db, user, payload.name)
    body = ApiTokenCreated(token=ApiTokenOut.model_validate(token), plaintext=plaintext)
    return await guard.commit(201, body.model_dump(mode="json"))


@router.post("/{token_id}/revoke", response_model=ApiTokenOut)
async def revoke_token(token_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ApiTokenOut:
    return ApiTokenOut.model_validate(await identity.revoke_api_token(db, user, token_id))
