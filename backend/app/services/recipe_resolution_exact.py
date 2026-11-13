"""Accept every exact standard-list match in the recipe-name queue at once (03, 3B).

A queued name that is a standard entry's own name, its plural or one of its listed
spellings means that entry; the curated list settles it, so a person accepts them
together instead of one click each. A name that only matches by head noun and
qualifiers stays in the queue for a person to look at.
"""

from __future__ import annotations

import uuid

from sqlalchemy import distinct, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models import AppUser, Recipe, RecipeIngredient
from app.schemas.catalog import IngredientCreate
from app.schemas.recipes import ResolveDecisionIn
from app.services import recipe_resolution


async def accept_exact(db: AsyncSession, user: AppUser) -> int:
    """Resolve each queued name whose standard proposal is exact. Returns how many."""
    names = (
        await db.execute(
            select(distinct(RecipeIngredient.name_norm))
            .join(Recipe, Recipe.id == RecipeIngredient.recipe_id)
            .where(*recipe_resolution._QUEUED)
        )
    ).scalars()
    ctx = await recipe_resolution.load_context(db)
    exact = []
    for name_norm in names:
        found = await recipe_resolution._standard_tier(db, ctx, name_norm)
        if found and found[0].exact:
            exact.append((name_norm, found[0]))
    # Two names can mean one entry ("rutabaga", "swede"): the
    # first creates its ingredient and the rest are matched to it.
    made: dict[str, uuid.UUID] = {}
    resolved = 0
    for name_norm, p in exact:
        chosen = p.ingredient_id or made.get(p.standard_key or "")
        decision = (
            ResolveDecisionIn(name_norm=name_norm, ingredient_id=chosen)
            if chosen
            else ResolveDecisionIn(
                name_norm=name_norm,
                ingredient=IngredientCreate(name=p.name, standard_key=p.standard_key),
            )
        )
        try:
            out = await recipe_resolution.decide(db, user, decision)
        except ApiError as exc:
            if exc.status_code == 409:
                # Its spelling or name is another ingredient's (alias_taken): one for a
                # person to settle, so it stays in the queue and the rest go on.
                await db.rollback()
                continue
            if exc.status_code != 404:
                raise
            # An earlier decision's learned spelling already settled it: still one of ours.
        else:
            if out.ingredient is not None and p.standard_key:
                made.setdefault(p.standard_key, out.ingredient.id)
        resolved += 1
    return resolved
