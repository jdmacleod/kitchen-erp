"""Linking recipe references to the recipes they name (07, 3B; #526).

After a scan's rows are written, one pass sets ``ref_recipe_id`` on every
recipe-reference line (``resolution = 'recipe'``): the recipe at the path the
reference names (``app.recipes.refs.target_path``) when one is there and not
missing, else NULL. Links follow paths only. A target that moves unlinks its
referrers, which read ``no_recipe`` with the old path until the referring file
is edited; a recipe created at the old path links them again. The app never
rewrites the recipes repository. The pass covers every reference line, not
only the changed files, so a new or moved target is seen at once.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Recipe, RecipeIngredient
from app.recipes.refs import target_path

log = get_logger(__name__)


@dataclass(frozen=True)
class MissingRef:
    referrer_path: str
    ref_path: str
    target: str | None  # None when the path leaves the recipes root


@dataclass
class LinkResult:
    linked: int = 0
    unlinked: int = 0
    missing: list[MissingRef] = field(default_factory=list)
    # Referring recipes whose link changed: their costs follow the new target.
    changed: set[uuid.UUID] = field(default_factory=set)


async def link_recipe_refs(db: AsyncSession) -> LinkResult:
    """Set or clear ``ref_recipe_id`` on every recipe-reference line. Flushes, never commits."""
    result = LinkResult()
    lines = (
        await db.execute(
            select(RecipeIngredient, Recipe.path)
            .join(Recipe, Recipe.id == RecipeIngredient.recipe_id)
            .where(RecipeIngredient.resolution == "recipe")
        )
    ).all()
    if not lines:
        return result
    by_path = dict(
        (await db.execute(select(Recipe.path, Recipe.id).where(Recipe.status != "missing"))).all()
    )
    for line, referrer_path in lines:
        target = target_path(referrer_path, line.ref_path or "")
        found = by_path.get(target) if target is not None else None
        if found is None:
            result.missing.append(MissingRef(referrer_path, line.ref_path or "", target))
        if found != line.ref_recipe_id:
            if found is None:
                result.unlinked += 1
            else:
                result.linked += 1
            line.ref_recipe_id = found
            result.changed.add(line.recipe_id)
    await db.flush()
    log.info(
        "recipe_refs linked=%d unlinked=%d missing=%d",
        result.linked,
        result.unlinked,
        len(result.missing),
    )
    return result
