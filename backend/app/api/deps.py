"""Request dependencies: database session, current user, admin gate, idempotency.

Scoped tokens (spec 03 §1F) are refused by default (eng review R1):

```
request ─▶ optional_user (session | token and its scopes)
  ├─ route uses CurrentUser ─▶ a session or a "*" token; any narrower token ─▶ 403
  └─ route uses scoped_user(s) ─▶ a session, a "*" token or a token holding s; else 403
```

So a route nobody marked can never be reached with a narrow token.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
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


FULL_SCOPE = "*"
SCOPES = ("vendors:read", "vendors:suggest")


def token_scopes(request: Request) -> tuple[str, ...] | None:
    """The scopes of the token behind this request, or None for a session."""
    return getattr(request.state, "token_scopes", None)


def token_id(request: Request) -> uuid.UUID | None:
    return getattr(request.state, "token_id", None)


async def optional_user(request: Request, db: DbSession) -> AppUser | None:
    request.state.token_scopes = None
    request.state.token_id = None
    cookie = request.cookies.get(get_settings().cookie_name)
    if cookie:
        user = await identity.user_for_session(db, cookie)
        if user is not None:
            return user
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        holder = await identity.holder_for_api_token(
            db, authorization.removeprefix("Bearer ").strip()
        )
        if holder is None:
            return None
        request.state.token_scopes = holder.scopes
        request.state.token_id = holder.token_id
        return holder.user
    return None


def _refused(scope: str | None) -> ApiError:
    needed = "full access" if scope is None else scope
    return ApiError(
        403,
        "insufficient_scope",
        f"This token cannot do that; it needs {needed}.",
        {"required": scope or FULL_SCOPE},
    )


async def current_user(
    request: Request, user: Annotated[AppUser | None, Depends(optional_user)]
) -> AppUser:
    if user is None:
        raise ApiError(401, "unauthenticated", "Authentication required.")
    scopes = token_scopes(request)
    if scopes is not None and FULL_SCOPE not in scopes:
        raise _refused(None)
    return user


def scoped_user(scope: str) -> Callable[..., Awaitable[AppUser]]:
    """A dependency that also lets in a token holding ``scope`` (and nothing else)."""

    async def dependency(
        request: Request, user: Annotated[AppUser | None, Depends(optional_user)]
    ) -> AppUser:
        if user is None:
            raise ApiError(401, "unauthenticated", "Authentication required.")
        scopes = token_scopes(request)
        if scopes is not None and FULL_SCOPE not in scopes and scope not in scopes:
            raise _refused(scope)
        return user

    dependency.__name__ = f"scoped_user_{scope.replace(':', '_')}"
    return dependency


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
