"""Seed and load the unit table. The conversion library itself never touches the database."""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.units import UnitRow
from app.units.table import SEED_UNITS, Unit


async def seed_units(db: AsyncSession) -> int:
    """Upsert every seed unit and remove any code not in the seed. Idempotent."""
    codes = [u.code for u in SEED_UNITS]
    for unit in SEED_UNITS:
        stmt = insert(UnitRow).values(
            code=unit.code,
            dimension=unit.dimension,
            to_base_factor=unit.to_base_factor,
            system=unit.system,
            aliases=list(unit.aliases),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[UnitRow.code],
            set_={
                "dimension": stmt.excluded.dimension,
                "to_base_factor": stmt.excluded.to_base_factor,
                "system": stmt.excluded.system,
                "aliases": stmt.excluded.aliases,
            },
        )
        await db.execute(stmt)
    await db.execute(delete(UnitRow).where(UnitRow.code.not_in(codes)))
    await db.commit()
    return len(codes)


async def load_units(db: AsyncSession) -> Mapping[str, Unit]:
    rows = (await db.execute(select(UnitRow).order_by(UnitRow.code))).scalars()
    return {
        r.code: Unit(r.code, r.dimension, r.to_base_factor, r.system, tuple(r.aliases))
        for r in rows
    }
