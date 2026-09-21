"""Line resolution ladder (Phase 2D). The ingest pipeline calls `resolve_purchase`
after the lines stage; until 2D lands it leaves every line `unmatched`."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession


async def resolve_purchase(db: AsyncSession, purchase_id: uuid.UUID) -> None:
    """Run the ladder over every item line of the purchase. Idempotent."""
    return None
