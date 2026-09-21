"""Request dependencies: database session, current user, admin gate, idempotency."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.errors import ApiError
from app.core.idempotency import IdempotencyGuard, build_guard
from app.models import AppUser
from app.services import identity

DbSession = Annotated[AsyncSession, Depends(get_session)]


async def optional_user(request: Request, db: DbSession) -> AppUser | None:
    cookie = request.cookies.get(get_settings().cookie_name)
    if cookie:
        user = await identity.user_for_session(db, cookie)
        if user is not None:
            return user
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        return await identity.user_for_api_token(db, authorization.removeprefix("Bearer ").strip())
    return None


async def current_user(user: Annotated[AppUser | None, Depends(optional_user)]) -> AppUser:
    if user is None:
        raise ApiError(401, "unauthenticated", "Authentication required.")
    return user


async def current_admin(user: Annotated[AppUser, Depends(current_user)]) -> AppUser:
    if user.role != "admin":
        raise ApiError(403, "forbidden", "Administrator role required.")
    return user


CurrentUser = Annotated[AppUser, Depends(current_user)]
CurrentAdmin = Annotated[AppUser, Depends(current_admin)]
OptionalUser = Annotated[AppUser | None, Depends(optional_user)]


async def idempotency(request: Request, user: CurrentUser, db: DbSession) -> IdempotencyGuard:
    return await build_guard(request, user, db)


Idempotency = Annotated[IdempotencyGuard, Depends(idempotency)]
