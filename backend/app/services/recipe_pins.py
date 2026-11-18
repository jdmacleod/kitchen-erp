"""Recipe pins (07, 3C, criterion 17): a recipe line's name held to one product.

Pins live in ``recipe_pin`` keyed by the recipe and the name as normalized, so
they survive re-indexing while the name in the file is unchanged. A merged
product or ingredient answers with its survivor.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models import Ingredient, Product, Recipe, RecipeIngredient, RecipePin
from app.schemas.recipes import RecipePinOut
from app.services import recipe_cost_triggers

# --- pins ------------------------------------------------------------------------------


async def pins_out(db: AsyncSession, recipe_id: uuid.UUID) -> list[RecipePinOut]:
    rows = await db.execute(
        select(RecipePin.name_norm, Product.id, Product.name, Product.brand)
        .join(Product, Product.id == RecipePin.product_id)
        .where(RecipePin.recipe_id == recipe_id)
        .order_by(RecipePin.name_norm)
    )
    return [
        RecipePinOut(name_norm=name_norm, product_id=product_id, product_name=name, brand=brand)
        for name_norm, product_id, name, brand in rows
    ]


async def _recipe(db: AsyncSession, recipe_id: uuid.UUID) -> Recipe:
    row = await db.get(Recipe, recipe_id)
    if row is None:
        raise ApiError(404, "not_found", "No such recipe.")
    return row


async def _survivor_product(db: AsyncSession, product_id: uuid.UUID) -> Product:
    product = await db.get(Product, product_id)
    if product is None:
        raise ApiError(404, "not_found", "No such product.")
    for _ in range(8):  # merges repoint one level, so this is one hop in practice
        if product.merged_into is None:
            break
        product = await db.get(Product, product.merged_into) or product
    return product


async def _survivor_ingredient(db: AsyncSession, ingredient_id: uuid.UUID) -> Ingredient | None:
    ingredient = await db.get(Ingredient, ingredient_id)
    for _ in range(8):
        if ingredient is None or ingredient.merged_into is None:
            break
        ingredient = await db.get(Ingredient, ingredient.merged_into)
    return ingredient


async def set_pin(
    db: AsyncSession, recipe_id: uuid.UUID, name_norm: str, product_id: uuid.UUID
) -> Recipe:
    """Pin the recipe's lines with this name to a product that fulfils their ingredient.

    Fulfilment is flat (07, "Hierarchy stays flat"): the product's ingredient,
    after following ``merged_into``, must be the line's. The pin stores the
    surviving product when a merged one is named.
    """
    recipe = await _recipe(db, recipe_id)
    lines = (
        (
            await db.execute(
                select(RecipeIngredient).where(
                    RecipeIngredient.recipe_id == recipe.id,
                    RecipeIngredient.name_norm == name_norm,
                )
            )
        )
        .scalars()
        .all()
    )
    if not lines:
        raise ApiError(404, "no_such_line", "No line in this recipe has that name.")
    resolved = {
        line.ingredient_id
        for line in lines
        if line.ingredient_id is not None and line.resolution in ("alias", "manual")
    }
    if not resolved:
        raise ApiError(
            409, "line_unresolved", "Resolve the line to an ingredient before pinning a product."
        )
    product = await _survivor_product(db, product_id)
    ingredient = await _survivor_ingredient(db, product.ingredient_id)
    if ingredient is None or ingredient.id not in resolved:
        line_ingredient = await db.get(Ingredient, next(iter(resolved)))
        raise ApiError(
            409,
            "product_does_not_fulfil",
            "That product is not of the line's ingredient.",
            details={
                "product_ingredient": ingredient.name if ingredient else None,
                "line_ingredient": line_ingredient.name if line_ingredient else None,
            },
        )
    pin = (
        await db.execute(
            select(RecipePin).where(
                RecipePin.recipe_id == recipe.id, RecipePin.name_norm == name_norm
            )
        )
    ).scalar_one_or_none()
    if pin is None:
        db.add(RecipePin(recipe_id=recipe.id, name_norm=name_norm, product_id=product.id))
    else:
        pin.product_id = product.id
    await db.flush()
    await recipe_cost_triggers.after_pin_changed(db, recipe.id)
    await db.commit()
    await db.refresh(recipe)
    return recipe


async def delete_pin(db: AsyncSession, recipe_id: uuid.UUID, name_norm: str) -> None:
    recipe = await _recipe(db, recipe_id)
    pin = (
        await db.execute(
            select(RecipePin).where(
                RecipePin.recipe_id == recipe.id, RecipePin.name_norm == name_norm
            )
        )
    ).scalar_one_or_none()
    if pin is None:
        raise ApiError(404, "not_found", "No pin on that line.")
    await db.delete(pin)
    await db.flush()
    await recipe_cost_triggers.after_pin_changed(db, recipe.id)
    await db.commit()
