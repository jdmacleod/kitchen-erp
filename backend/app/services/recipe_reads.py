"""Reading and tidying indexed recipes (07, 3A, 3D): the list with each recipe's
default cost, one recipe with its lines and pins, confirming a relink, and
removing a recipe whose file is gone. ``services/recipes`` keeps the index.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models import Ingredient, Recipe, RecipeIngredient
from app.schemas.recipes import RecipeIngredientOut, RecipeListItem, RecipeOut, RelinkProposal
from app.services import recipe_cost_triggers, recipe_cost_views, recipe_resolution


async def list_recipes(
    db: AsyncSession,
    *,
    status: str | None = None,
    dirty: bool | None = None,
    q: str | None = None,
    completeness: str | None = None,
) -> list[RecipeListItem]:
    """Every recipe with its default ``latest`` cost when its current content has one (3D).

    Ordered by title, then path (UI-7.3). ``q`` matches the title or the path, case
    folded. ``completeness`` keeps recipes whose current cost has every line priced
    (``complete``) or not (``incomplete``: some line unpriced, or no snapshot yet).
    """
    stmt = select(Recipe).order_by(func.lower(Recipe.title), Recipe.path)
    if status is not None:
        stmt = stmt.where(Recipe.status == status)
    if dirty is not None:
        stmt = stmt.where(Recipe.dirty.is_(dirty))
    if q:
        needle = f"%{_escape_like(q.strip())}%"
        stmt = stmt.where(
            Recipe.title.ilike(needle, escape="\\") | Recipe.path.ilike(needle, escape="\\")
        )
    rows = (await db.execute(stmt)).scalars().all()
    costs = await recipe_cost_views.summaries(db, list(rows))
    items = [
        RecipeListItem.model_validate(row).model_copy(update={"cost": costs.get(row.id)})
        for row in rows
    ]
    if completeness == "complete":
        items = [i for i in items if i.cost is not None and i.cost.complete]
    elif completeness == "incomplete":
        items = [i for i in items if i.cost is None or not i.cost.complete]
    return items


def _escape_like(text: str) -> str:
    """Escape the LIKE metacharacters so a typed ``%`` or ``_`` matches itself."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def get_recipe(db: AsyncSession, recipe_id: uuid.UUID) -> Recipe:
    row = await db.get(Recipe, recipe_id)
    if row is None:
        raise ApiError(404, "not_found", "No such recipe.")
    return row


async def recipe_out(db: AsyncSession, row: Recipe) -> RecipeOut:
    relink: RelinkProposal | None = None
    if row.relink_candidate_id is not None:
        target = await db.get(Recipe, row.relink_candidate_id)
        if target is not None:
            relink = RelinkProposal(
                target_id=target.id,
                path=target.path,
                title=target.title,
                reason=row.relink_reason or "",
            )
    lines = (
        (
            await db.execute(
                select(RecipeIngredient)
                .where(RecipeIngredient.recipe_id == row.id)
                .order_by(RecipeIngredient.seq)
            )
        )
        .scalars()
        .all()
    )
    ingredient_ids = {line.ingredient_id for line in lines if line.ingredient_id is not None}
    names: dict[uuid.UUID, str] = {}
    if ingredient_ids:
        found = await db.execute(
            select(Ingredient.id, Ingredient.name).where(Ingredient.id.in_(ingredient_ids))
        )
        names = dict(found.all())
    # Column by column: the row's `ingredients` and `pins` relationships are not
    # loaded, and reading them here would lazy-load outside the session's greenlet.
    columns = {
        name: getattr(row, name)
        for name in RecipeOut.model_fields
        if name not in ("relink", "ingredients", "pins")
    }
    columns["body"] = _linked_body(row.body, {line.seq: line.ref_recipe_id for line in lines})
    return RecipeOut(
        **columns,
        relink=relink,
        ingredients=[
            RecipeIngredientOut.model_validate(line).model_copy(
                update={"ingredient_name": names.get(line.ingredient_id)}
            )
            for line in lines
        ],
        pins=await recipe_resolution.pins_out(db, row.id),
    )


def _linked_body(
    body: list[dict[str, Any]] | None, links: dict[int, uuid.UUID | None]
) -> list[dict[str, Any]] | None:
    """The stored body with each recipe reference's ``recipe_id`` filled in (#526)."""
    if body is None or not any(links.values()):
        return body
    return [
        {
            **section,
            "steps": [
                [
                    {**item, "recipe_id": links.get(item.get("seq"))}
                    if item.get("t") == "ingredient" and item.get("ref_path")
                    else item
                    for item in step
                ]
                for step in section.get("steps", [])
            ],
        }
        for section in body
    ]


async def relink(db: AsyncSession, recipe_id: uuid.UUID, target_id: uuid.UUID) -> Recipe:
    """Confirm that a missing recipe became ``target``: the old row takes the new
    file, its parse and its ingredient rows, and the new row goes, so pins and
    history stay with the recipe."""
    row = await get_recipe(db, recipe_id)
    if row.status != "missing":
        raise ApiError(409, "not_missing", "Only a missing recipe can be relinked.")
    if target_id == recipe_id:
        raise ApiError(409, "same_recipe", "A recipe cannot be relinked to itself.")
    target = await db.get(Recipe, target_id)
    if target is None:
        raise ApiError(404, "not_found", "No such target recipe.")
    if target.status == "missing":
        raise ApiError(409, "target_missing", "The target recipe has no file either.")
    carried = {
        name: getattr(target, name)
        for name in (
            "path",
            "title",
            "servings",
            "servings_text",
            "content_hash",
            "head_commit",
            "dirty",
            "parse_error_message",
            "front_matter",
            "body",
            "makes_qty",
            "makes_unit",
            "makes_text",
            "last_indexed_at",
            "last_seen_at",
        )
    }
    # The target's ingredient rows become the recipe's and the rows of its last
    # good parse at the old path go. A target that never parsed has no rows, and
    # then the recipe keeps its last good ones (criterion 4), with the target's
    # parse error on it.
    target_lines = await db.scalar(
        select(func.count())
        .select_from(RecipeIngredient)
        .where(RecipeIngredient.recipe_id == target.id)
    )
    if target_lines:
        await db.execute(delete(RecipeIngredient).where(RecipeIngredient.recipe_id == row.id))
        await db.execute(
            update(RecipeIngredient)
            .where(RecipeIngredient.recipe_id == target.id)
            .values(recipe_id=row.id)
        )
    await db.delete(target)
    await db.flush()  # the path must be free before the old row takes it
    for name, value in carried.items():
        setattr(row, name, value)
    row.status = "parse_error" if carried["parse_error_message"] else "ok"
    row.relink_candidate_id = None
    row.relink_reason = None
    await db.flush()
    # The recipe's content is the new file's now; its history at the old hash stays.
    await recipe_cost_triggers.after_recipe_changed(db, row.id)
    await db.commit()
    await db.refresh(row)
    return row


async def delete_recipe(db: AsyncSession, recipe_id: uuid.UUID) -> None:
    """Remove a recipe explicitly; only one whose file is gone (3A)."""
    row = await get_recipe(db, recipe_id)
    if row.status != "missing":
        raise ApiError(409, "not_missing", "Only a missing recipe can be removed.")
    await db.delete(row)
    await db.commit()
