"""Recompute triggers for cost snapshots (07, 3D; package 6b).

Snapshots are recomputed for a recipe when its hash changes, when an alias or
pin affecting it changes, and when a bridge or price that a priced line used
changes. Each entry point finds the recipes affected with one query and
recomputes every snapshot key those recipes have (``latest`` at least), the way
``price_norm`` is recomputed for an ingredient's dependents: flushed inside the
caller's transaction, so the fresh snapshot lands with the change that made
the old one stale, or not at all. ``commit=True`` is for callers that have
already committed, as the catalog's bridge hooks have.

A trigger never raises out of its caller. Each recipe is recomputed under a
savepoint; a failure is logged with the recipe's id and rolled back to the
savepoint, the caller's transaction stands, and the stale snapshot waits for
the next trigger or ``kerp recompute-costs``. The log line carries identifiers
only, never recipe text.

With no recipe indexed, every entry point is one indexed query that finds
nothing, so the receipt worker and the price book pay nothing for Phase 3.

Which recipes a change reaches:

- a recipe's own content, pins, or a relink: that recipe;
- an alias decision: every recipe with a line of the decided name, and (the
  decision re-runs the lookup) every recipe whose waiting line settled;
- an ingredient's density, measures or canonical unit: every recipe with a
  line of that ingredient, whichever product priced it, since the line's own
  conversion crossed the bridge even when the price did not;
- a product's pack or density override: every recipe with a line of the
  product's ingredient (``cheapest`` may now choose it), plus pins to it;
- prices observed, voided or renormalized: every recipe with a line of the
  observations' products' ingredients, plus snapshots whose lines used them;
- a merge: the recipes whose rows it repointed.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Iterable

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import (
    Ingredient,
    PriceObservation,
    Product,
    PurchaseLine,
    Recipe,
    RecipeCostLine,
    RecipeCostSnapshot,
    RecipeIngredient,
    RecipePin,
)
from app.services import recipe_costing

log = get_logger(__name__)

# --- the recompute itself ---------------------------------------------------------------


async def _recompute(db: AsyncSession, recipe_ids: Iterable[uuid.UUID], *, commit: bool) -> int:
    """Recompute the recipes' snapshots, each under its own savepoint. The number
    of snapshots recomputed."""
    ids = sorted(set(recipe_ids), key=str)
    if not ids:
        return 0
    done = 0
    for recipe_id in ids:
        try:
            async with db.begin_nested():
                done += await recipe_costing.recompute_recipe_core(db, recipe_id)
        except Exception:
            log.exception("recipe cost recompute failed", extra={"recipe_id": str(recipe_id)})
    if commit:
        await db.commit()
    return done


async def _find(db: AsyncSession, what: str, finder) -> set[uuid.UUID]:
    """The affected recipes, found under a savepoint; none when the query fails."""
    try:
        async with db.begin_nested():
            return set(await finder())
    except Exception:
        log.exception("recipe cost trigger failed", extra={"trigger": what})
        return set()


async def _guarded(db: AsyncSession, what: str, finder, *, commit: bool) -> int:
    """Find the affected recipes, then recompute them."""
    return await _recompute(db, await _find(db, what, finder), commit=commit)


# --- which recipes ----------------------------------------------------------------------


async def _recipes_for_ingredients(
    db: AsyncSession, ingredient_ids: Collection[uuid.UUID]
) -> set[uuid.UUID]:
    """Recipes with a line resolved to one of the ingredients, or to an ingredient
    merged into one of them (a merge repoints lines, but a stale pointer costs
    nothing to cover)."""
    ids = {i for i in ingredient_ids if i is not None}
    if not ids:
        return set()
    merged = select(Ingredient.id).where(Ingredient.merged_into.in_(ids))
    rows = await db.execute(
        select(RecipeIngredient.recipe_id)
        .where(
            or_(RecipeIngredient.ingredient_id.in_(ids), RecipeIngredient.ingredient_id.in_(merged))
        )
        .distinct()
    )
    return set(rows.scalars())


async def _recipes_with_snapshot_lines(db: AsyncSession, where) -> set[uuid.UUID]:
    rows = await db.execute(
        select(RecipeCostSnapshot.recipe_id)
        .join(RecipeCostLine, RecipeCostLine.snapshot_id == RecipeCostSnapshot.id)
        .where(where)
        .distinct()
    )
    return set(rows.scalars())


async def _survivor_product(db: AsyncSession, product_id: uuid.UUID) -> Product | None:
    product = await db.get(Product, product_id)
    for _ in range(8):
        if product is None or product.merged_into is None:
            break
        product = await db.get(Product, product.merged_into)
    return product


async def _ingredients_of_observations(
    db: AsyncSession, observation_ids: Collection[uuid.UUID]
) -> set[uuid.UUID]:
    """The ingredients the observations' products fulfil, as the survivor when a
    product was merged (its prices report under the survivor's ingredient)."""
    rows = await db.execute(
        select(Product.ingredient_id, Product.merged_into)
        .join(PriceObservation, PriceObservation.product_id == Product.id)
        .where(PriceObservation.id.in_(list(observation_ids)))
        .distinct()
    )
    ingredients: set[uuid.UUID] = set()
    survivors: set[uuid.UUID] = set()
    for ingredient_id, merged_into in rows:
        if merged_into is not None:
            survivors.add(merged_into)
        else:
            ingredients.add(ingredient_id)
    if survivors:
        more = await db.execute(select(Product.ingredient_id).where(Product.id.in_(survivors)))
        ingredients.update(more.scalars())
    return ingredients


# --- entry points -----------------------------------------------------------------------


async def after_recipe_changed(db: AsyncSession, recipe_id: uuid.UUID, *, commit: bool = False):
    """The recipe's content hash changed, or a relink gave it another file's content."""
    return await _recompute(db, [recipe_id], commit=commit)


async def after_recipes_changed(
    db: AsyncSession, recipe_ids: Iterable[uuid.UUID], *, commit: bool = False
) -> int:
    return await _recompute(db, recipe_ids, commit=commit)


async def after_pin_changed(db: AsyncSession, recipe_id: uuid.UUID, *, commit: bool = False):
    """A pin on one of the recipe's lines was set, replaced or removed."""
    return await _recompute(db, [recipe_id], commit=commit)


async def after_merge(
    db: AsyncSession, recipe_ids: Iterable[uuid.UUID], *, commit: bool = False
) -> int:
    """A product or ingredient merge repointed rows of these recipes (07, 3C)."""
    return await _recompute(db, recipe_ids, commit=commit)


async def after_names_changed(
    db: AsyncSession, name_norms: Collection[str], *, commit: bool = False
) -> int:
    """An alias decision settled these normalized names: every recipe with a line of one."""
    names = {n for n in name_norms if n}

    async def find() -> set[uuid.UUID]:
        if not names:
            return set()
        rows = await db.execute(
            select(RecipeIngredient.recipe_id)
            .where(RecipeIngredient.name_norm.in_(names))
            .distinct()
        )
        return set(rows.scalars())

    return await _guarded(db, "names_changed", find, commit=commit)


async def unmatched_lines(db: AsyncSession) -> frozenset[uuid.UUID]:
    """The lines still waiting for a person, taken before a lookup pass so
    ``after_lines_settled`` can see which of them the pass settled."""
    rows = await db.execute(
        select(RecipeIngredient.id).where(RecipeIngredient.resolution == "unmatched")
    )
    return frozenset(rows.scalars())


async def settled_recipes(db: AsyncSession, before: Collection[uuid.UUID]) -> set[uuid.UUID]:
    """The recipes whose lines in ``before`` stopped being unmatched: a decision's
    own lines, and any other waiting line the re-run lookup settled."""

    async def find() -> set[uuid.UUID]:
        if not before:
            return set()
        rows = await db.execute(
            select(RecipeIngredient.recipe_id)
            .where(
                RecipeIngredient.id.in_(list(before)),
                RecipeIngredient.resolution != "unmatched",
            )
            .distinct()
        )
        return set(rows.scalars())

    return await _find(db, "lines_settled", find)


async def after_lines_settled(
    db: AsyncSession, before: Collection[uuid.UUID], *, commit: bool = False
) -> int:
    """Recompute ``settled_recipes``."""
    return await _recompute(db, await settled_recipes(db, before), commit=commit)


async def after_ingredient_bridge_changed(
    db: AsyncSession, ingredient_id: uuid.UUID, *, commit: bool = False
) -> int:
    """A density, named measure or canonical unit of the ingredient changed
    (criterion 25): every recipe with a line of it, and no other."""

    async def find() -> set[uuid.UUID]:
        return await _recipes_for_ingredients(db, [ingredient_id])

    return await _guarded(db, "ingredient_bridge", find, commit=commit)


async def _recipes_of_products(
    db: AsyncSession, product_ids: Collection[uuid.UUID], *, ingredient_id: uuid.UUID | None
) -> set[uuid.UUID]:
    """Recipes pinned to one of the products or whose snapshots used one, plus
    every recipe with a line of ``ingredient_id`` when given."""
    ids: set[uuid.UUID] = set()
    if ingredient_id is not None:
        ids |= await _recipes_for_ingredients(db, [ingredient_id])
    pinned = await db.execute(
        select(RecipePin.recipe_id).where(RecipePin.product_id.in_(list(product_ids))).distinct()
    )
    ids.update(pinned.scalars())
    ids |= await _recipes_with_snapshot_lines(db, RecipeCostLine.product_id.in_(list(product_ids)))
    return ids


async def recipes_of_product(
    db: AsyncSession, product_id: uuid.UUID, *, other_ingredient: bool
) -> set[uuid.UUID]:
    """The recipes a product merge touches, taken before the merge repoints them:
    pins to the loser, snapshots whose lines used it and, when the loser fulfilled
    another ingredient than the survivor, every recipe with a line of that one."""
    product = await db.get(Product, product_id)
    ingredient_id = product.ingredient_id if product is not None and other_ingredient else None
    return await _find(
        db,
        "product_merge",
        lambda: _recipes_of_products(db, [product_id], ingredient_id=ingredient_id),
    )


async def after_product_bridge_changed(
    db: AsyncSession, product_id: uuid.UUID, *, commit: bool = False
) -> int:
    """A pack, pieces or density override of the product changed, or it moved to
    another ingredient: every recipe with a line of its ingredient, every recipe
    pinned to it, and every snapshot whose lines used it."""

    async def find() -> set[uuid.UUID]:
        product = await _survivor_product(db, product_id)
        products = {product_id} | ({product.id} if product is not None else set())
        return await _recipes_of_products(
            db, products, ingredient_id=product.ingredient_id if product is not None else None
        )

    return await _guarded(db, "product_bridge", find, commit=commit)


async def after_prices_changed(
    db: AsyncSession, observation_ids: Collection[uuid.UUID], *, commit: bool = False
) -> int:
    """Observations were added, voided or renormalized: every snapshot whose lines
    used one, and (for ``latest``, ``cheapest`` and ``average``) every recipe with
    a line of an ingredient their products fulfil."""
    ids = [i for i in observation_ids if i is not None]

    async def find() -> set[uuid.UUID]:
        if not ids:
            return set()
        ingredients = await _ingredients_of_observations(db, ids)
        found = await _recipes_for_ingredients(db, ingredients)
        found |= await _recipes_with_snapshot_lines(db, RecipeCostLine.observation_id.in_(ids))
        return found

    return await _guarded(db, "prices_changed", find, commit=commit)


async def after_purchase_changed(
    db: AsyncSession, purchase_id: uuid.UUID, *, commit: bool = False
) -> int:
    """A purchase was committed, recommitted or reopened: its lines' prices start
    or stop qualifying together, so every observation of its lines counts as changed."""

    async def observations() -> list[uuid.UUID]:
        rows = await db.execute(
            select(PriceObservation.id)
            .join(PurchaseLine, PurchaseLine.id == PriceObservation.purchase_line_id)
            .where(PurchaseLine.purchase_id == purchase_id)
        )
        return list(rows.scalars())

    try:
        async with db.begin_nested():
            ids = await observations()
    except Exception:
        log.exception("recipe cost trigger failed", extra={"trigger": "purchase_changed"})
        return 0
    return await after_prices_changed(db, ids, commit=commit)


# --- the provisional flag (criterion 24) ------------------------------------------------


async def mark_committed(db: AsyncSession, recipe: Recipe) -> None:
    """The file was committed unchanged: the snapshots of its content stand as
    committed, without recomputation, and name the commit that holds it."""
    await _set_provisional(db, recipe, False)


async def mark_provisional(db: AsyncSession, recipe: Recipe) -> None:
    """The file is unchanged on disk but no longer what HEAD holds."""
    await _set_provisional(db, recipe, True)


async def _set_provisional(db: AsyncSession, recipe: Recipe, flag: bool) -> None:
    try:
        async with db.begin_nested():
            await db.execute(
                update(RecipeCostSnapshot)
                .where(
                    RecipeCostSnapshot.recipe_id == recipe.id,
                    RecipeCostSnapshot.content_hash == recipe.content_hash,
                    RecipeCostSnapshot.provisional.is_(not flag),
                )
                .values(provisional=flag, head_commit=recipe.head_commit)
                .execution_options(synchronize_session=False)
            )
    except Exception:
        log.exception("recipe cost flag update failed", extra={"recipe_id": str(recipe.id)})
