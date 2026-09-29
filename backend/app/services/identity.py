"""Users, sessions, and API tokens. Routers stay thin; the rules live here."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
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


async def _get_user(db: AsyncSession, user_id: uuid.UUID) -> AppUser:
    # populate_existing: after waiting on a lock, read what the other
    # transaction committed rather than the state loaded before it.
    user = await db.get(AppUser, user_id, populate_existing=True)
    if user is None:
        raise ApiError(404, "not_found", "No such user.")
    return user


async def _lock_active_admins(db: AsyncSession) -> int:
    """Count the active admins, holding their rows until this transaction ends.

    Two admins removing each other at the same moment would otherwise both
    count two, both pass, and leave none. With the rows locked, the second
    waits for the first to commit and then counts again (READ COMMITTED
    re-checks a locked row's WHERE clause once the lock is granted).
    """
    stmt = (
        select(AppUser.id)
        .where(AppUser.role == "admin", AppUser.active.is_(True))
        .with_for_update()
    )
    return len((await db.execute(stmt)).all())


async def _end_access(db: AsyncSession, user_id: uuid.UUID, *, keep_session: str | None = None):
    """Revoke a user's sessions, all of them or all but the one given.

    Deactivating a member or setting their password must take effect at once,
    not when an old cookie expires. Flushed; the caller commits.
    """
    now = _now()
    sessions = update(Session).where(Session.user_id == user_id, Session.revoked_at.is_(None))
    if keep_session is not None:
        sessions = sessions.where(Session.token_hash != digest(keep_session))
    await db.execute(sessions.values(revoked_at=now))


async def edit_user(db: AsyncSession, actor: AppUser, user_id: uuid.UUID, changes: dict) -> AppUser:
    """An admin changes a member's name, email, role or whether they can sign in.

    The household can never lose its last active admin, and an admin cannot
    demote or deactivate themselves (another admin can): either would be a
    one-click lockout with no way back but the command line.
    """
    touches_access = changes.get("role") is not None or changes.get("active") is not None
    admins = await _lock_active_admins(db) if touches_access else 0
    user = await _get_user(db, user_id)
    demoting = changes.get("role") == "member" and user.role == "admin"
    deactivating = changes.get("active") is False and user.active
    if user.id == actor.id and (demoting or deactivating):
        raise ApiError(
            409,
            "self_lockout",
            "You can't remove your own admin access. Another admin can.",
        )
    removing_admin = (demoting or deactivating) and user.role == "admin" and user.active
    if removing_admin and admins <= 1:
        raise ApiError(409, "last_admin", "The household needs at least one active admin.")
    if changes.get("email") is not None:
        user.email = str(changes["email"]).strip().lower()
    if changes.get("display_name") is not None:
        user.display_name = changes["display_name"].strip()
    if changes.get("role") is not None:
        user.role = changes["role"]
    if changes.get("active") is not None:
        user.active = changes["active"]
    if deactivating:
        await _end_access(db, user.id)
        await db.execute(
            update(ApiToken)
            .where(ApiToken.user_id == user.id, ApiToken.revoked_at.is_(None))
            .values(revoked_at=_now())
        )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ApiError(409, "email_taken", "A user with that email already exists.") from exc
    await db.refresh(user)
    return user


async def set_password(
    db: AsyncSession, actor: AppUser, user_id: uuid.UUID, password: str
) -> AppUser:
    """An admin sets a new password for another member and signs them out everywhere."""
    if user_id == actor.id:
        raise ApiError(
            409,
            "use_change_password",
            "Change your own password in Settings, System, with your current one.",
        )
    user = await _get_user(db, user_id)
    user.password_hash = hash_password(password)
    await _end_access(db, user.id)
    await db.commit()
    await db.refresh(user)
    return user


async def change_own_password(
    db: AsyncSession, user: AppUser, current: str, new: str, *, session_secret: str | None
) -> None:
    """Change your own password; every other session of yours is signed out."""
    if not verify_password(user.password_hash, current):
        raise ApiError(422, "wrong_password", "The current password is not right.")
    user.password_hash = hash_password(new)
    await _end_access(db, user.id, keep_session=session_secret)
    await db.commit()


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


async def create_api_token(
    db: AsyncSession, user: AppUser, name: str, scopes: list[str] | None = None
) -> tuple[ApiToken, str]:
    plaintext = new_api_token()
    token = ApiToken(
        user_id=user.id,
        name=name.strip(),
        token_hash=digest(plaintext),
        scopes=list(scopes) if scopes else ["*"],
    )
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


@dataclass(frozen=True)
class TokenHolder:
    """The user a bearer token stands for, and what that token may do."""

    user: AppUser
    token_id: uuid.UUID
    scopes: tuple[str, ...]


async def holder_for_api_token(db: AsyncSession, plaintext: str) -> TokenHolder | None:
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
    return TokenHolder(user=user, token_id=token.id, scopes=tuple(token.scopes))


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
