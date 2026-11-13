"""Confirm an ingredient's unconfirmed bridges together (issue 356).

On the Unconfirmed values list a person checks one ingredient's density and its
measures against a label in one sitting, so they confirm them together. A value
no food could have (``catalog.bridge_check``) is left for a person to fix, never
confirmed in a batch.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models.catalog import Ingredient, IngredientMeasure
from app.services import recipe_cost_triggers, unconfirmed_bridges


async def confirm_ingredient(db: AsyncSession, ingredient_id: uuid.UUID) -> int:
    """Confirm the ingredient's plausible unconfirmed density and measures. Returns how many."""
    ingredient = await db.get(Ingredient, ingredient_id)
    if ingredient is None:
        raise ApiError(404, "not_found", "No such ingredient.")
    waiting = [
        v
        for v in await unconfirmed_bridges.unconfirmed(db)
        if v.ingredient_id == ingredient_id and v.kind != "product_density" and v.problem is None
    ]
    for value in waiting:
        if value.kind == "density":
            ingredient.density_confirmed = True
        else:
            measure = await db.get(IngredientMeasure, value.id)
            if measure is not None:
                measure.confirmed = True
    await db.commit()
    if waiting:
        await recipe_cost_triggers.after_ingredient_bridge_changed(db, ingredient_id, commit=True)
    return len(waiting)
