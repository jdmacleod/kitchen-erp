"""The ingredient page's "Made by recipe" (07, 3D; #526): the detail read and a link's
aftermath.

The detail adds the linked recipe (what it makes, its cost per unit) and, when
none is linked and no product has a price, the one recipe titled like the
ingredient to offer. A PATCH that sets or clears the link commits it first, then
recomputes every recipe with a line of the ingredient, and the recipes that cost
from those, in a transaction of its own: a failed recompute leaves the link in
place and is counted. A link that closes a cycle is kept and says so.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Ingredient
from app.schemas.catalog import IngredientOut
from app.services import recipe_cost_triggers, recipe_source_views

log = get_logger(__name__)


async def detail_out(db: AsyncSession, ingredient: Ingredient) -> IngredientOut:
    out = IngredientOut.model_validate(ingredient)
    out.made_by_recipe = await recipe_source_views.made_by_out(db, ingredient)
    out.recipe_match = await recipe_source_views.recipe_match(db, ingredient)
    return out


async def after_link(db: AsyncSession, ingredient: Ingredient) -> IngredientOut:
    """Recompute after a committed link change; the read with what that did."""
    try:
        result = await recipe_cost_triggers.after_ingredient_bridge_changed(
            db, ingredient.id, commit=True
        )
        recomputed, failed = result.recipes_ok, result.recipes_failed
    except Exception:
        log.exception("made-by recompute failed", extra={"ingredient_id": str(ingredient.id)})
        await db.rollback()
        recomputed, failed = 0, 1
    await db.refresh(ingredient)
    out = await detail_out(db, ingredient)
    out.recomputed, out.recompute_failed = recomputed, failed
    if ingredient.made_by_recipe_id is not None:
        warning = await recipe_source_views.cycle_warning(db, ingredient.made_by_recipe_id)
        out.warnings = [warning] if warning else []
    return out
