"""What the Cook screens need beyond the index (07, 3E; package 8).

The rendered recipe's body: the parsed file as plain JSON, written at index
time beside the ``recipe_ingredient`` rows and served on the recipe. Dismissing
a relink proposal with "Not the same". The recipes whose lines resolve to an
ingredient, for the hub's "Used in" card (criterion 33).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models import Ingredient, Recipe, RecipeIngredient
from app.recipes.cooklang import (
    Cookware,
    IngredientRef,
    Quantity,
    QuantityNumber,
    QuantityRange,
    QuantityText,
    Text,
    Timer,
)
from app.recipes.cooklang import Recipe as ParsedRecipe
from app.schemas.recipes import IngredientRecipesOut, IngredientRecipeUse

# --- the body ------------------------------------------------------------------------


def _qty(quantity: Quantity) -> str | None:
    """The quantity as text: "2", "1–2", "a handful"; None when there is none."""
    if isinstance(quantity, QuantityNumber):
        return str(quantity.value)
    if isinstance(quantity, QuantityRange):
        return f"{quantity.low}–{quantity.high}"
    if isinstance(quantity, QuantityText):
        return quantity.text
    return None


def serialize_body(parsed: ParsedRecipe) -> list[dict[str, Any]]:
    """The parsed file as plain JSON: ``[{"name", "steps": [[item, …], …]}, …]``.

    Items are ``{"t": "text", "v"}``, ``{"t": "ingredient", "name", "qty",
    "unit", "note", "seq"}`` (``seq`` is the matching ``recipe_ingredient``
    row, numbered from 1 in document order as ``_ingredient_rows`` does),
    ``{"t": "cookware", "name", "qty"}`` and ``{"t": "timer", "name", "qty",
    "unit"}``. Decimals are strings; nothing here is a float.
    """
    seq = 0
    sections: list[dict[str, Any]] = []
    for section in parsed.sections:
        steps: list[list[dict[str, Any]]] = []
        for step in section.steps:
            items: list[dict[str, Any]] = []
            for item in step.items:
                if isinstance(item, Text):
                    items.append({"t": "text", "v": item.value})
                elif isinstance(item, IngredientRef):
                    seq += 1
                    items.append(
                        {
                            "t": "ingredient",
                            "name": item.raw_name,
                            "qty": _qty(item.quantity),
                            "unit": item.unit_text,
                            "note": item.note,
                            "seq": seq,
                        }
                    )
                elif isinstance(item, Cookware):
                    items.append({"t": "cookware", "name": item.name, "qty": _qty(item.quantity)})
                elif isinstance(item, Timer):
                    items.append(
                        {
                            "t": "timer",
                            "name": item.name,
                            "qty": _qty(item.quantity),
                            "unit": item.unit_text,
                        }
                    )
            steps.append(items)
        sections.append({"name": section.name, "steps": steps})
    return sections


# --- relink dismissals ------------------------------------------------------------------


async def dismiss_relink(db: AsyncSession, recipe_id: uuid.UUID) -> Recipe:
    """ "Not the same": clear the proposal and remember the candidate's path, so a
    later scan that meets a new file there never proposes it for this recipe again.

    Proposals are minted when a file first appears in the index (3A, step 3),
    so the path is what can come back, not the candidate's row.
    """
    row = await db.get(Recipe, recipe_id)
    if row is None:
        raise ApiError(404, "not_found", "No such recipe.")
    if row.relink_candidate_id is None:
        raise ApiError(409, "no_proposal", "This recipe has no relink proposal to dismiss.")
    candidate = await db.get(Recipe, row.relink_candidate_id)
    dismissed = list(row.relink_dismissed_paths or [])
    if candidate is not None and candidate.path not in dismissed:
        dismissed.append(candidate.path)
    row.relink_dismissed_paths = dismissed
    row.relink_candidate_id = None
    row.relink_reason = None
    await db.commit()
    await db.refresh(row)
    return row


def is_dismissed(row: Recipe, candidate_path: str) -> bool:
    return candidate_path in (row.relink_dismissed_paths or [])


# --- an ingredient's recipes -------------------------------------------------------------


def quantity_as_written(line: RecipeIngredient) -> str | None:
    """ "2 cups", "1–2 tsp", "a handful", or None for a bare name."""
    if line.qty_kind == "text":
        qty = line.qty_text
    elif line.qty_kind == "range" and line.qty is not None:
        qty = f"{_plain(line.qty)}–{_plain(line.qty_high) if line.qty_high is not None else ''}"
    elif line.qty_kind == "number" and line.qty is not None:
        qty = _plain(line.qty)
    else:
        qty = None
    parts = [p for p in (qty, line.unit_text) if p]
    return " ".join(parts) or None


def _plain(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


async def _survivor_and_merged(db: AsyncSession, ingredient_id: uuid.UUID) -> set[uuid.UUID]:
    """The ingredient's surviving id and every ingredient merged into it."""
    ingredient = await db.get(Ingredient, ingredient_id)
    if ingredient is None:
        raise ApiError(404, "not_found", "No such ingredient.")
    for _ in range(8):  # merges repoint one level, so this is one hop in practice
        if ingredient.merged_into is None:
            break
        ingredient = await db.get(Ingredient, ingredient.merged_into) or ingredient
    merged = (
        await db.execute(select(Ingredient.id).where(Ingredient.merged_into == ingredient.id))
    ).scalars()
    return {ingredient.id, *merged}


async def recipes_using(db: AsyncSession, ingredient_id: uuid.UUID) -> IngredientRecipesOut:
    """Recipes whose lines resolve to the ingredient, through ``merged_into``,
    ordered by title, each with its lines' quantities as written."""
    ids = await _survivor_and_merged(db, ingredient_id)
    rows = await db.execute(
        select(Recipe, RecipeIngredient)
        .join(RecipeIngredient, RecipeIngredient.recipe_id == Recipe.id)
        .where(
            RecipeIngredient.ingredient_id.in_(ids),
            RecipeIngredient.resolution.in_(("alias", "manual")),
        )
        .order_by(Recipe.title, Recipe.path, RecipeIngredient.seq)
    )
    uses: dict[uuid.UUID, IngredientRecipeUse] = {}
    for recipe, line in rows:
        use = uses.get(recipe.id)
        if use is None:
            use = IngredientRecipeUse(
                id=recipe.id,
                title=recipe.title,
                path=recipe.path,
                status=recipe.status,
                quantities=[],
            )
            uses[recipe.id] = use
        use.quantities.append(quantity_as_written(line))
    items = sorted(uses.values(), key=lambda u: (u.title.casefold(), u.path))
    return IngredientRecipesOut(items=items, total=len(items))
