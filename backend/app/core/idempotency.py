"""Idempotency-Key handling for mutating endpoints a mobile client may retry.

Scope is the authenticated user. Same key + same request replays the stored
response; same key + different request is a 422; no header means no memory.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ApiError
from app.models import AppUser, IdempotencyKey

HEADER = "Idempotency-Key"


class IdempotencyGuard:
    def __init__(self, key: str | None, request_hash: str, user: AppUser, db: AsyncSession) -> None:
        self.key = key
        self.request_hash = request_hash
        self.user = user
        self.db = db
        self.replay: JSONResponse | None = None

    async def load(self) -> None:
        if self.key is None:
            return
        result = await self.db.execute(
            select(IdempotencyKey).where(
                IdempotencyKey.user_id == self.user.id, IdempotencyKey.key == self.key
            )
        )
        existing = result.scalar_one_or_none()
        if existing is None:
            return
        if existing.request_hash != self.request_hash:
            raise ApiError(
                422,
                "idempotency_key_reused",
                "This Idempotency-Key was already used with a different request.",
            )
        self.replay = JSONResponse(
            content=existing.response_body,
            status_code=existing.status_code,
            headers={"Idempotent-Replayed": "true"},
        )

    async def commit(self, status_code: int, body: dict[str, Any]) -> JSONResponse:
        """Store the response for this key and return it."""
        if self.key is not None:
            self.db.add(
                IdempotencyKey(
                    user_id=self.user.id,
                    key=self.key,
                    request_hash=self.request_hash,
                    status_code=status_code,
                    response_body=body,
                    created_at=datetime.now(UTC),
                )
            )
            try:
                await self.db.commit()
            except IntegrityError:
                # A concurrent retry stored it first; the response is the same by construction.
                await self.db.rollback()
        return JSONResponse(content=body, status_code=status_code)


async def build_guard(request: Request, user: AppUser, db: AsyncSession) -> IdempotencyGuard:
    raw = await request.body()
    request_hash = hashlib.sha256(
        request.method.encode() + b"\n" + request.url.path.encode() + b"\n" + raw
    ).hexdigest()
    guard = IdempotencyGuard(request.headers.get(HEADER), request_hash, user, db)
    await guard.load()
    return guard


async def sweep_expired(db: AsyncSession) -> int:
    cutoff = datetime.now(UTC) - timedelta(hours=get_settings().idempotency_ttl_hours)
    result = await db.execute(delete(IdempotencyKey).where(IdempotencyKey.created_at < cutoff))
    await db.commit()
    return result.rowcount or 0
