"""Rebuild derived prices when unit conversion changes (04, 2A; #437).

Every ``price_norm`` row records the ``CONVERT_VERSION`` that produced it. When
a deploy brings a new version, the stored conversions and the recipe cost
snapshots built on them are stale, so ``kerp migrate`` rebuilds both. Both are
derived data (non-negotiable 5): a rebuild loses nothing.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PriceNorm
from app.services import pricebook, recipe_costing
from app.units.convert import CONVERT_VERSION


async def stale_versions(db: AsyncSession) -> list[str]:
    """The conversion versions in ``price_norm`` other than the current one."""
    rows = await db.execute(
        select(PriceNorm.convert_version)
        .where(PriceNorm.convert_version != CONVERT_VERSION)
        .distinct()
        .order_by(PriceNorm.convert_version)
    )
    return list(rows.scalars())


async def refresh_if_stale(db: AsyncSession, say: Callable[[str], object]) -> bool:
    """Rebuild ``price_norm`` and then the recipe costs when any row is stale."""
    stale = await stale_versions(db)
    if not stale:
        return False
    say(
        f"Unit conversion is now version {CONVERT_VERSION}; prices were normalized "
        f"with version {', '.join(stale)}. Rebuilding prices and recipe costs…"
    )
    norms = await pricebook.recompute_all(db)
    recipes = await recipe_costing.recompute_all(db)
    say(f"  recomputed {norms} normalizations and costs for {recipes} recipes")
    return True
