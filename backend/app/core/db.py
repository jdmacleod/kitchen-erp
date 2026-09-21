"""Async engine and session for the runtime role."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


@dataclass
class _State:
    engine: AsyncEngine | None = None
    sessionmaker: async_sessionmaker[AsyncSession] | None = None


_state = _State()


def get_engine() -> AsyncEngine:
    if _state.engine is None:
        _state.engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
        _state.sessionmaker = async_sessionmaker(_state.engine, expire_on_commit=False)
    return _state.engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _state.sessionmaker is not None
    return _state.sessionmaker


async def dispose_engine() -> None:
    if _state.engine is not None:
        await _state.engine.dispose()
    _state.engine = None
    _state.sessionmaker = None


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session
