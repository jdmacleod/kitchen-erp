"""Seed and load the unit table. The conversion library itself never touches the database."""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catalog import Ingredient, Product
from app.models.units import UnitRow
from app.units import ConversionContext, Measure, Pack, ProductContext
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


# Builds the context the pure conversion library needs out of database rows.
# It lives here rather than in catalog because pricebook, resolution and catalog
# all need it, and keeping it in catalog made pricebook import catalog while
# catalog imported pricebook back inside a function body.
async def build_context(
    db: AsyncSession, ingredient: Ingredient, product: Product | None
) -> ConversionContext:
    units = await load_units(db)
    product_ctx = None
    if product is not None:
        product_ctx = ProductContext(
            pack=Pack(product.pack_qty, product.pack_unit)
            if product.pack_qty is not None
            else None,
            density_g_per_ml=product.density_override,
            density_source=product.density_override_source,
            density_confirmed=product.density_override_confirmed,
        )
    return ConversionContext(
        canonical_unit=ingredient.canonical_unit,
        density_g_per_ml=ingredient.density_g_per_ml,
        density_source=ingredient.density_source,
        density_confirmed=ingredient.density_confirmed,
        measures=tuple(
            Measure(m.label, m.canonical_qty, m.source, m.confirmed) for m in ingredient.measures
        ),
        product=product_ctx,
        units=units,
    )
