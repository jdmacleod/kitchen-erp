"""Per-user settings (17, Phase 4A): the current kitchen the client sends on
kitchen-scoped calls. No endpoint reads it on the server; the client resolves
the kitchen and sends it, as 09 requires."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models import AppUser
from app.models.geo import HomeBase
from app.models.plan import UserSetting
from app.schemas.plan import SettingsIn, SettingsOut


async def _suggested(db: AsyncSession):
    """The only kitchen when there is exactly one; otherwise nothing to suggest."""
    if (await db.execute(select(func.count()).select_from(HomeBase))).scalar_one() != 1:
        return None
    return (await db.execute(select(HomeBase.id))).scalar_one()


async def get_settings(db: AsyncSession, user: AppUser) -> SettingsOut:
    row = await db.get(UserSetting, user.id)
    return SettingsOut(
        current_home_base_id=row.current_home_base_id if row else None,
        suggested_home_base_id=await _suggested(db),
    )


async def put_settings(db: AsyncSession, user: AppUser, payload: SettingsIn) -> SettingsOut:
    """Set the current kitchen, refusing one that does not exist; null clears it."""
    if (
        payload.current_home_base_id is not None
        and await db.get(HomeBase, payload.current_home_base_id) is None
    ):
        raise ApiError(
            422,
            "unknown_home_base",
            "No such kitchen.",
            {"home_base_id": str(payload.current_home_base_id)},
        )
    row = await db.get(UserSetting, user.id)
    if row is None:
        row = UserSetting(user_id=user.id)
        db.add(row)
    row.current_home_base_id = payload.current_home_base_id
    await db.commit()
    return await get_settings(db, user)
