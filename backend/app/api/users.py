from __future__ import annotations

import uuid

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.api.deps import CurrentAdmin, DbSession, Idempotency
from app.schemas.identity import PasswordSet, UserCreate, UserEdit, UserList, UserOut
from app.services import identity

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=UserList)
async def list_users(_: CurrentAdmin, db: DbSession) -> UserList:
    return UserList(items=[UserOut.model_validate(u) for u in await identity.list_users(db)])


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate, _: CurrentAdmin, db: DbSession, guard: Idempotency
) -> JSONResponse:
    if guard.replay is not None:
        return guard.replay
    user = await identity.create_user(
        db,
        email=payload.email,
        display_name=payload.display_name,
        password=payload.password,
        role=payload.role,
    )
    return await guard.commit(201, UserOut.model_validate(user).model_dump(mode="json"))


@router.patch("/{user_id}", response_model=UserOut)
async def edit_user(
    user_id: uuid.UUID, payload: UserEdit, actor: CurrentAdmin, db: DbSession
) -> UserOut:
    changes = payload.model_dump(exclude_unset=True)
    return UserOut.model_validate(await identity.edit_user(db, actor, user_id, changes))


@router.post("/{user_id}/password", response_model=UserOut)
async def set_password(
    user_id: uuid.UUID, payload: PasswordSet, actor: CurrentAdmin, db: DbSession
) -> UserOut:
    """Set a new password for another member; it signs them out everywhere."""
    return UserOut.model_validate(await identity.set_password(db, actor, user_id, payload.password))
