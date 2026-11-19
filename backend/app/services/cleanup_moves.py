"""Tidy the catalog's move check (#525): a bottle filed under the generic word
after a more specific standard entry arrived.

A product whose ingredient is less specific than a standard entry its name names
(``catalog.specificity``) is offered a move to the ingredient holding that entry:
"Harbor Distillers Bourbon Whiskey" under whiskey goes to bourbon. A ``guess``,
accepted one at a time, after its reason names the recipe lines of the old
ingredient that would lose their only price and the lines pinned to the product
(the pin is kept: the person may have pinned the bottle on purpose).

The count pass (``guess=False``, the inbox's landing row) is one query for the
products plus one for the ingredients holding entries, with the matching in
memory. A stand-in until ingredients have a parent (#525's ``parent_id``).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import specificity, standard
from app.models import (
    Recipe,
    RecipeCostLine,
    RecipeCostSnapshot,
    RecipeIngredient,
    RecipePin,
)
from app.models.catalog import Ingredient, Product
from app.schemas.catalog import ProductUpdate
from app.services import catalog, recipe_cost_triggers
from app.services.cleanup_suggestion import Consequence, Suggestion, fingerprint, suggestion_key

_PRODUCTS = (
    select(
        Product.id,
        Product.name,
        Product.ingredient_id,
        Ingredient.name.label("ingredient_name"),
        Ingredient.slug,
    )
    .join(Ingredient, Ingredient.id == Product.ingredient_id)
    .where(Product.active, Ingredient.active, Ingredient.merged_into.is_(None))
    .order_by(Product.name)
)

# The products of these ingredients with any price, real or posted, that costing
# could take: a line costed from one product keeps a price if another has one.
_PRICED_SQL = """
    SELECT DISTINCT p.ingredient_id, p.id AS product_id
    FROM offer_applicable_all oa
    JOIN product p ON p.id = oa.product_id AND p.active
    WHERE p.ingredient_id = ANY(CAST(:ids AS uuid[])) AND oa.norm_unit_price IS NOT NULL
"""


@dataclass(frozen=True)
class _Line:
    line_id: str
    recipe_id: uuid.UUID
    text: str


async def product_moves(
    db: AsyncSession,
    *,
    guess: bool = True,
    product_id: uuid.UUID | None = None,
    rows: list | None = None,
) -> list[Suggestion]:
    """Every move the catalog offers, or the one for ``product_id``. ``rows`` are
    the products as Tidy the catalog already read them (id, name, ingredient_id,
    ingredient_name, slug), so the count reads products once. ``guess=False``
    leaves out the consequences, which only the full listing and an accept need."""
    if rows is None:
        query = _PRODUCTS if product_id is None else _PRODUCTS.where(Product.id == product_id)
        rows = list((await db.execute(query)).all())
    if not rows:
        return []
    holders = {
        slug: (iid, name)
        for iid, name, slug in await db.execute(
            select(Ingredient.id, Ingredient.name, Ingredient.slug).where(
                Ingredient.active, Ingredient.merged_into.is_(None)
            )
        )
    }
    entries = standard.by_key()
    candidates: dict[uuid.UUID, list[standard.StandardEntry]] = {}
    found: list[tuple[object, standard.StandardEntry, uuid.UUID, str]] = []
    for row in rows:
        if row.ingredient_id not in candidates:
            own = entries.get(row.slug)
            forms = (row.ingredient_name, *(specificity.entry_forms(own) if own else ()))
            candidates[row.ingredient_id] = [
                e
                for e in specificity.narrower_entries(forms, row.slug)
                if e.key in holders and holders[e.key][0] != row.ingredient_id
            ]
        if not candidates[row.ingredient_id]:
            continue
        best = specificity.best_move(row.name, candidates[row.ingredient_id])
        if best is not None:
            found.append((row, best, *holders[best.key]))
    lost = await _consequences(db, [r for r, *_ in found]) if guess and found else {}
    return [_suggestion(row, entry, iid, name, lost.get(row.id)) for row, entry, iid, name in found]


def _suggestion(row, entry, target_id, target_name, lines) -> Suggestion:
    general = row.ingredient_name
    priced, pinned = lines or ((), ())
    reason = f"its name says {entry.name}, a kind of {general}"
    if priced:
        n = len(priced)
        reason = (
            f"{reason}; {n} recipe {'line' if n == 1 else 'lines'} naming {general} "
            f"{'loses its' if n == 1 else 'lose their'} price; pin "
            f"{'it' if n == 1 else 'them'} or log a {general} price"
        )
    consequences = tuple(
        [Consequence(f"{line.text} loses its price", line.recipe_id) for line in priced]
        + [
            Consequence(f"{line.text} stays pinned to it, now under {target_name}", line.recipe_id)
            for line in pinned
        ]
    )
    return Suggestion(
        suggestion_key("product_move", row.id, str(target_id)),
        "product_move",
        row.id,
        row.name,
        general,
        target_name,
        reason,
        str(target_id),
        other_id=target_id,
        confidence="guess",
        consequences=consequences,
        fingerprint=fingerprint(
            target_id, row.ingredient_id, *sorted(line.line_id for line in (*priced, *pinned))
        ),
    )


async def _consequences(
    db: AsyncSession, rows: list
) -> dict[uuid.UUID, tuple[list[_Line], list[_Line]]]:
    """For each product: the recipe lines of its ingredient whose latest snapshot is
    priced or posted from it while no other product of the ingredient has a price,
    and the lines pinned to it. Read from the latest snapshots."""
    ids = [r.id for r in rows]
    ingredient_of = {r.id: r.ingredient_id for r in rows}
    priced_by: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    found = await db.execute(
        text(_PRICED_SQL), {"ids": [str(i) for i in set(ingredient_of.values())]}
    )
    for ingredient_id, product_id in found:
        priced_by[ingredient_id].add(product_id)
    out: dict[uuid.UUID, tuple[list[_Line], list[_Line]]] = {i: ([], []) for i in ids}
    used = await db.execute(
        select(RecipeCostLine.id, RecipeCostLine.status, RecipeCostLine.observation_id,
               RecipeCostLine.product_id, RecipeIngredient.raw_name, Recipe.id, Recipe.title)
        .join(RecipeCostSnapshot, RecipeCostSnapshot.id == RecipeCostLine.snapshot_id)
        .join(Recipe, (Recipe.id == RecipeCostSnapshot.recipe_id)
              & (Recipe.content_hash == RecipeCostSnapshot.content_hash))
        .join(RecipeIngredient, RecipeIngredient.id == RecipeCostLine.recipe_ingredient_id)
        .where(
            RecipeCostLine.product_id.in_(ids),
            RecipeCostLine.status.in_(("priced", "posted")),
            RecipeCostSnapshot.basis == "latest",
            RecipeCostSnapshot.window_days.is_(None),
            RecipeCostSnapshot.min_quality.is_(None),
        )
        .order_by(Recipe.title, RecipeIngredient.seq)
    )  # fmt: skip
    for line_id, status, obs, product_id, raw, recipe_id, title in used:
        if priced_by[ingredient_of[product_id]] - {product_id}:
            continue  # another product of it still prices the line
        out[product_id][0].append(_Line(f"{line_id}:{status}:{obs}", recipe_id, f"{title}: {raw}"))
    pins = await db.execute(
        select(RecipePin.product_id, RecipePin.name_norm, Recipe.id, Recipe.title)
        .join(Recipe, Recipe.id == RecipePin.recipe_id)
        .where(RecipePin.product_id.in_(ids))
        .order_by(Recipe.title, RecipePin.name_norm)
    )
    for product_id, name_norm, recipe_id, title in pins:
        out[product_id][1].append(
            _Line(f"pin:{recipe_id}:{name_norm}", recipe_id, f"{title}: {name_norm}")
        )
    return out


async def lock(db: AsyncSession, product_id: uuid.UUID) -> None:
    """Hold the product's row until the accept commits: a second accept waits, then
    finds the move gone."""
    await db.execute(select(Product.id).where(Product.id == product_id).with_for_update())


async def accept(db: AsyncSession, s: Suggestion) -> dict[str, int]:
    """Move the product, in the caller's transaction (no commit). The old
    ingredient's recipes are captured before the move, as a product merge does:
    an ``average`` or ``cheapest`` snapshot there that never used this product's
    price would otherwise stay stale."""
    before = await recipe_cost_triggers.recipes_of_product(db, s.subject_id, other_ingredient=True)
    await catalog.update_product_core(
        db, s.subject_id, ProductUpdate(ingredient_id=uuid.UUID(s.value))
    )
    after = await recipe_cost_triggers.recipes_of_product(db, s.subject_id, other_ingredient=True)
    await recipe_cost_triggers.after_recipes_changed(db, before - after)
    return {"recipes_recomputed": len(before | after)}
