"""Users, sessions, and API tokens. Routers stay thin; the rules live here."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.security import digest, hash_password, new_api_token, new_secret, verify_password
from app.models import ApiToken, AppUser, Session


def _now() -> datetime:
    return datetime.now(UTC)


# --- users -----------------------------------------------------------------


async def create_user(
    db: AsyncSession, *, email: str, display_name: str, password: str, role: str
) -> AppUser:
    user = AppUser(
        email=email.strip().lower(),
        display_name=display_name.strip(),
        password_hash=hash_password(password),
        role=role,
        active=True,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ApiError(409, "email_taken", "A user with that email already exists.") from exc
    await db.refresh(user)
    return user


async def list_users(db: AsyncSession) -> list[AppUser]:
    result = await db.execute(select(AppUser).order_by(AppUser.created_at))
    return list(result.scalars())


async def count_users(db: AsyncSession) -> int:
    return int((await db.execute(select(func.count()).select_from(AppUser))).scalar_one())


async def authenticate(db: AsyncSession, email: str, password: str) -> AppUser | None:
    result = await db.execute(select(AppUser).where(AppUser.email == email.strip().lower()))
    user = result.scalar_one_or_none()
    if user is None or not user.active or not verify_password(user.password_hash, password):
        return None
    return user


# --- sessions --------------------------------------------------------------


async def open_session(db: AsyncSession, user: AppUser) -> str:
    """Create a session and return the cookie secret (never stored in clear)."""
    secret = new_secret()
    now = _now()
    session = Session(
        user_id=user.id,
        token_hash=digest(secret),
        created_at=now,
        expires_at=now + timedelta(days=get_settings().session_ttl_days),
        last_seen_at=now,
    )
    db.add(session)
    await db.commit()
    return secret


async def user_for_session(db: AsyncSession, secret: str) -> AppUser | None:
    now = _now()
    result = await db.execute(
        select(Session, AppUser)
        .join(AppUser, AppUser.id == Session.user_id)
        .where(Session.token_hash == digest(secret))
    )
    row = result.first()
    if row is None:
        return None
    session, user = row
    if session.revoked_at is not None or session.expires_at <= now or not user.active:
        return None
    # Sliding window: touch at most once a minute to keep writes rare.
    if now - session.last_seen_at > timedelta(minutes=1):
        session.last_seen_at = now
        session.expires_at = now + timedelta(days=get_settings().session_ttl_days)
        await db.commit()
    return user


async def revoke_session(db: AsyncSession, secret: str) -> None:
    await db.execute(
        update(Session)
        .where(Session.token_hash == digest(secret), Session.revoked_at.is_(None))
        .values(revoked_at=_now())
    )
    await db.commit()


async def revoke_all_sessions(db: AsyncSession, user_id: uuid.UUID) -> int:
    result = await db.execute(
        update(Session)
        .where(Session.user_id == user_id, Session.revoked_at.is_(None))
        .values(revoked_at=_now())
    )
    await db.commit()
    return result.rowcount or 0


# --- API tokens ------------------------------------------------------------


async def create_api_token(db: AsyncSession, user: AppUser, name: str) -> tuple[ApiToken, str]:
    plaintext = new_api_token()
    token = ApiToken(user_id=user.id, name=name.strip(), token_hash=digest(plaintext))
    db.add(token)
    await db.commit()
    await db.refresh(token)
    return token, plaintext


async def list_api_tokens(db: AsyncSession, user: AppUser) -> list[ApiToken]:
    result = await db.execute(
        select(ApiToken).where(ApiToken.user_id == user.id).order_by(ApiToken.created_at)
    )
    return list(result.scalars())


async def revoke_api_token(db: AsyncSession, user: AppUser, token_id: uuid.UUID) -> ApiToken:
    token = await db.get(ApiToken, token_id)
    if token is None or token.user_id != user.id:
        raise ApiError(404, "not_found", "No such token.")
    if token.revoked_at is None:
        token.revoked_at = _now()
        await db.commit()
        await db.refresh(token)
    return token


async def user_for_api_token(db: AsyncSession, plaintext: str) -> AppUser | None:
    result = await db.execute(
        select(ApiToken, AppUser)
        .join(AppUser, AppUser.id == ApiToken.user_id)
        .where(ApiToken.token_hash == digest(plaintext))
    )
    row = result.first()
    if row is None:
        return None
    token, user = row
    if token.revoked_at is not None or not user.active:
        return None
    token.last_used_at = _now()
    await db.commit()
    return user
