"""The last Transcribe again that closed on a purchase (04, 2S; issue 491).

A receipt still read only in part says it was tried, and how it ended, so the
person knows the photo is the one place left to read the missing lines from.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LineReread
from app.models.purchases import REREAD_OPEN


async def last_transcribe(db: AsyncSession, purchase_id: uuid.UUID) -> dict[str, Any] | None:
    found = (
        await db.execute(
            select(LineReread)
            .where(
                LineReread.purchase_id == purchase_id,
                LineReread.mode == "transcribe",
                LineReread.status.not_in(REREAD_OPEN),
            )
            .order_by(LineReread.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return None if found is None else {"status": found.status, "at": found.created_at}
