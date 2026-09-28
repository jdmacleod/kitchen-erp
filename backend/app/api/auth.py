from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from app.api.deps import CurrentUser, DbSession
from app.core.config import get_settings
from app.core.errors import ApiError
from app.schemas.identity import LoginIn, LoginOut, PasswordChange, UserOut
from app.services import identity

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_cookie(response: Response, secret: str) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.cookie_name,
        secret,
        max_age=settings.session_ttl_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def _clear_cookie(response: Response) -> None:
    response.delete_cookie(get_settings().cookie_name, path="/")


@router.post("/login", response_model=LoginOut)
async def login(payload: LoginIn, response: Response, db: DbSession) -> LoginOut:
    user = await identity.authenticate(db, payload.email, payload.password)
    if user is None:
        raise ApiError(401, "invalid_credentials", "Email or password is incorrect.")
    secret = await identity.open_session(db, user)
    _set_cookie(response, secret)
    return LoginOut(user=UserOut.model_validate(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, db: DbSession) -> Response:
    cookie = request.cookies.get(get_settings().cookie_name)
    if cookie:
        await identity.revoke_session(db, cookie)
    out = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_cookie(out)
    return out


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(user: CurrentUser, db: DbSession) -> Response:
    await identity.revoke_all_sessions(db, user.id)
    out = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_cookie(out)
    return out


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: PasswordChange, request: Request, user: CurrentUser, db: DbSession
) -> Response:
    """Change your own password. This session stays signed in; every other one ends."""
    await identity.change_own_password(
        db,
        user,
        payload.current_password,
        payload.new_password,
        session_secret=request.cookies.get(get_settings().cookie_name),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)
