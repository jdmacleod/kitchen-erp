"""The recipe-line lookup (07, 3C, VC3) and the context it reads.

A line resolves without a person only through an ingredient's own normalized
name or one of its spellings, exactly, or through an inflection of either; the
context also carries the negligible and ignored names. ``services/recipe_resolution``
describes the rest: the queue, the cascade and decisions.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.names import normalize_name, singulars
from app.core.config import Settings, get_settings
from app.models import Ingredient, RecipeIngredient, RecipeNameIgnore
from app.services.catalog import IngredientIndex, ingredient_index

# --- the lookup ------------------------------------------------------------------------


@dataclass(frozen=True)
class Context:
    """Everything one lookup pass reads, loaded once for however many lines.

    ``ask_model`` lets the cascade's model tier call the local model. The queue
    listing never does (a model call per queued name would take minutes);
    :func:`ask` does, for one name a person asked about.
    """

    index: IngredientIndex
    negligible: frozenset[str]
    ignored: frozenset[str]
    ask_model: bool = False


def negligible_names(settings: Settings | None = None) -> frozenset[str]:
    settings = settings or get_settings()
    return frozenset(
        key for key in (normalize_name(name) for name in settings.recipes_negligible_names) if key
    )


async def load_context(
    db: AsyncSession, settings: Settings | None = None, *, ask_model: bool = False
) -> Context:
    ignored = (await db.execute(select(RecipeNameIgnore.name_norm))).scalars().all()
    return Context(
        index=await ingredient_index(db),
        negligible=negligible_names(settings),
        ignored=frozenset(ignored),
        ask_model=ask_model,
    )


def _exact(index: IngredientIndex, key: str) -> Ingredient | None:
    """The one active ingredient whose normalized name or spelling is ``key``.

    Two active ingredients sharing a key (a name that normalizes like another's,
    which the catalog's unique index on the lowercased name does not forbid)
    offer nothing: a wrong resolution costs more than a queued name.
    """
    found: dict[uuid.UUID, Ingredient] = {ing.id: ing for ing in index.by_norm.get(key, [])}
    for ing in index.by_spelling.get(key, []):
        found.setdefault(ing.id, ing)
    if len(found) == 1:
        return next(iter(found.values()))
    return None


def lookup(ctx: Context, name_norm: str) -> Ingredient | None:
    """An exact name or spelling, then an inflection: the only steps without a person."""
    hit = _exact(ctx.index, name_norm)
    if hit is not None:
        return hit
    for single in singulars(name_norm):
        hit = _exact(ctx.index, single)
        if hit is not None:
            return hit
    return None


def apply_lookup(ctx: Context, row: RecipeIngredient) -> bool:
    """Settle one unmatched row from the context; True when it stopped being unmatched."""
    if row.name_norm in ctx.negligible:
        row.negligible = True
        row.resolution = "negligible"
        row.ingredient_id = None
        return True
    if row.name_norm in ctx.ignored:
        row.resolution = "ignored"
        row.ingredient_id = None
        return True
    hit = lookup(ctx, row.name_norm)
    if hit is None:
        return False
    row.ingredient_id = hit.id
    row.resolution = "alias"
    return True
