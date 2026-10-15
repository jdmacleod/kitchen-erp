"""Liquids measured in g, and changing an ingredient's unit safely (issue 358).

A new ingredient used to be measured in g whatever it was. Juices, wines and
syrups bought by volume then sat on Needs a bridge as "no density" until
someone found one. ``volume_only`` finds the ingredients whose every product
with a pack is sold by volume, so their bridge row can offer "Measure in ml
instead". The change itself is the ordinary unit change on the ingredient,
which recomputes its prices.

A named measure is stored in the ingredient's unit ("1 bottle = 750 g"), and
with no density it can't be converted, so a unit change would leave it saying
750 ml. ``guard_unit_change`` refuses that unless the request drops the
measures. This is the same for every caller, the ingredient page included.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models.catalog import Ingredient, IngredientMeasure, Product
from app.schemas.catalog import IngredientUpdate
from app.units import is_volume


async def volume_only(db: AsyncSession, ingredient_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    """Of these ingredients, the ones in g whose every active product with a pack
    is sold by volume (and that have at least one), in one query."""
    ids = set(ingredient_ids)
    if not ids:
        return set()
    rows = await db.execute(
        select(Product.ingredient_id, Product.pack_unit)
        .join(Ingredient, Ingredient.id == Product.ingredient_id)
        .where(
            Product.ingredient_id.in_(ids),
            Product.active,
            Product.pack_unit.is_not(None),
            Ingredient.canonical_unit == "g",
        )
    )
    packs: dict[uuid.UUID, list[str]] = defaultdict(list)
    for ingredient_id, unit in rows.tuples():
        packs[ingredient_id].append(unit)
    return {i for i, units in packs.items() if all(is_volume(u) for u in units)}


async def guard_unit_change(
    db: AsyncSession, ingredient_id: uuid.UUID, payload: IngredientUpdate
) -> None:
    """Refuse a unit change while measures in the old unit exist, unless the
    request drops them; then delete them in the same transaction as the change."""
    if payload.canonical_unit is None:
        return
    ingredient = await db.get(Ingredient, ingredient_id)
    if ingredient is None or ingredient.canonical_unit == payload.canonical_unit:
        return  # a missing ingredient is the update's own 404
    measures = (
        (
            await db.execute(
                select(IngredientMeasure)
                .where(IngredientMeasure.ingredient_id == ingredient_id)
                .order_by(IngredientMeasure.label)
            )
        )
        .scalars()
        .all()
    )
    if not measures:
        return
    if not payload.drop_measures:
        unit = ingredient.canonical_unit
        raise ApiError(
            409,
            "measures_in_old_unit",
            f"{ingredient.name}'s measures are in {unit} and can't be converted without a "
            f"density. Remove them to change the unit.",
            details={
                "unit": unit,
                "measures": [
                    {"label": m.label, "canonical_qty": format(m.canonical_qty, "f")}
                    for m in measures
                ],
            },
        )
    await db.execute(
        delete(IngredientMeasure).where(IngredientMeasure.ingredient_id == ingredient_id)
    )
