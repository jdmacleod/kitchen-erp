"""Tidy the catalog: fixes proposed in batches, applied only on a person's accept (#349).

The checks that keep new products and ingredients tidy (#342–#348) also find
what the catalog already holds that they would have prevented. Each finding is a
suggestion with a stable key: the kind, the row and what it proposes, so a skip
sticks until the data changes. Kinds:

- ``category``: an ingredient without a category, with the likeliest one, or one
  filed where its name says otherwise (a frozen food, a fish under meat); the
  person may choose another before accepting;
- ``ingredient_name``: an ingredient name the 1G rules write differently;
- ``usda_link``: a preferred USDA reference that doesn't fit, relinked to one
  that does or unlinked;
- ``ingredient_merge``: two ingredients that look like one food;
- ``product_name``: a product name that repeats its brand or pack;
- ``product_brand``: a brand that only says "none" or describes the food
  ("Organic"), a brand on loose produce (cleared: produce is sold by weight, the
  bunch or the piece, and the store's name is the vendor), or a brand on another
  loose product (which is then a branded or private-label one).

Each suggestion is ``sure`` or a ``guess`` (issue 372; ``cleanup_ingredients``).
Accepting several at once applies only the sure ones and the ones a person chose a
value for; a guess is accepted on its own. Accepting calls the existing services;
nothing else is written here, apart from the kept USDA guesses (``cleanup_guesses``).
"""

from __future__ import annotations

import uuid
from dataclasses import replace

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import categories as category_keys
from app.catalog import proposals as merging
from app.catalog.brand_names import is_never_brand
from app.catalog.conform import conform, proper_noun_words
from app.catalog.product_names import produce_name, short_name
from app.core.errors import ApiError
from app.models import AppUser
from app.models.catalog import CleanupSkip, Ingredient, IngredientRef, Product
from app.schemas.catalog import IngredientUpdate, ProductUpdate
from app.services import (
    catalog,
    cleanup_ingredients,
    ingredient_duplicates,
    ingredient_reconcile,
)
from app.services.cleanup_suggestion import Kind, Suggestion, suggestion_key
from app.services.ingredient_names import brand_keys

KINDS: tuple[Kind, ...] = (
    "ingredient_merge",
    "usda_link",
    "category",
    "ingredient_name",
    "product_name",
    "product_brand",
)


async def _ingredients(db: AsyncSession) -> list[Ingredient]:
    return list(
        (
            await db.execute(
                select(Ingredient)
                .where(Ingredient.active, Ingredient.merged_into.is_(None))
                .order_by(Ingredient.name)
            )
        ).scalars()
    )


def _names(rows: list[Ingredient], keys: set[str]) -> list[Suggestion]:
    out = []
    for i in rows:
        found = conform(i.name, brand_keys=keys)
        if found.name and found.name != i.name:
            out.append(
                Suggestion(
                    suggestion_key("ingredient_name", i.id, found.name),
                    "ingredient_name",
                    i.id,
                    i.name,
                    i.name,
                    found.name,
                    "; ".join(found.changes),
                    found.name,
                )
            )
    return out


async def _merges(db: AsyncSession) -> list[Suggestion]:
    out = []
    for pair in await ingredient_duplicates.find_duplicates(db):
        keep = pair.keep
        other = pair.b if keep is pair.a else pair.a
        out.append(
            Suggestion(
                suggestion_key("ingredient_merge", other.id, str(keep.id)),
                "ingredient_merge",
                other.id,
                other.name,
                f"{other.name} and {keep.name}",
                f"one ingredient, {keep.name}",
                ", ".join(pair.reasons),
                str(keep.id),
                other_id=keep.id,
            )
        )
    return out


async def _products(db: AsyncSession, keys: set[str]) -> list[Suggestion]:
    proper = proper_noun_words()
    out = []
    rows = await db.execute(
        select(
            Product.id,
            Product.name,
            Product.brand,
            Product.kind,
            Product.pack_qty,
            Product.brand_id,
            Ingredient.name.label("ingredient_name"),
            Ingredient.category,
        )
        .join(Ingredient, Ingredient.id == Product.ingredient_id)
        .where(Product.active)
        .order_by(Product.name)
    )
    for p in rows:
        loose = p.kind in ("loose", "random_weight")
        produce = category_keys.is_produce(p.category, p.ingredient_name)
        if p.brand and is_never_brand(p.brand):
            out.append(
                Suggestion(
                    suggestion_key("product_brand", p.id, ""),
                    "product_brand",
                    p.id,
                    p.name,
                    f"brand “{p.brand}”",
                    "no brand",
                    f"“{p.brand}” describes the food; it isn't a brand",
                    "",
                )
            )
        elif p.brand and loose and produce:
            out.append(
                Suggestion(
                    suggestion_key("product_brand", p.id, ""),
                    "product_brand",
                    p.id,
                    p.name,
                    f"brand “{p.brand}”",
                    "no brand, sold loose",
                    "produce sold loose has no brand; the store is the vendor",
                    "",
                )
            )
        elif p.brand and merging.is_null_like(p.brand):
            out.append(
                Suggestion(
                    suggestion_key("product_brand", p.id, ""),
                    "product_brand",
                    p.id,
                    p.name,
                    f"brand “{p.brand}”",
                    "no brand",
                    "the brand only says none",
                    "",
                )
            )
        elif p.brand and loose:
            # A brand the dataset knows is linked (brand_id), and is some store's own.
            kind = "private_label" if p.brand_id is not None else "branded"
            out.append(
                Suggestion(
                    suggestion_key("product_brand", p.id, kind),
                    "product_brand",
                    p.id,
                    p.name,
                    p.kind.replace("_", " "),
                    kind.replace("_", " "),
                    f"it has the brand {p.brand}",
                    kind,
                )
            )
        brand = None if p.brand and merging.is_null_like(p.brand) else p.brand
        short = short_name(
            p.name,
            brand,
            has_pack=p.pack_qty is not None,
            produce=produce,
            brand_keys=keys,
            proper_nouns=proper,
        )
        if short and short != p.name:
            filler = produce and produce_name(p.name) != p.name
            out.append(
                Suggestion(
                    suggestion_key("product_name", p.id, short),
                    "product_name",
                    p.id,
                    p.name,
                    p.name,
                    short,
                    "produce needs no “Fresh” or “1 ct”"
                    if filler
                    else "brand and pack have fields of their own",
                    short,
                )
            )
    return out


async def suggestions(db: AsyncSession, *, guess: bool = True) -> list[Suggestion]:
    """Every suggestion not skipped, by kind. ``guess=False`` is for counting: it skips
    the slow guesses (a category or a better USDA food for each) and the merges,
    which have a Needs you row of their own."""
    rows = await _ingredients(db)
    keys = await brand_keys(db)
    found = [
        *(await _merges(db) if guess else []),
        *await cleanup_ingredients.usda_links(db, rows, guess=guess),
        *await cleanup_ingredients.categories(db, rows, guess=guess),
        *_names(rows, keys),
        *await _products(db, keys),
    ]
    skipped = set((await db.execute(select(CleanupSkip.key))).scalars())
    return [s for s in found if s.key not in skipped]


async def count(db: AsyncSession) -> int:
    return len(await suggestions(db, guess=False))


async def skip(db: AsyncSession, user: AppUser, keys: list[str]) -> None:
    for key in keys:
        await db.execute(
            insert(CleanupSkip).values(key=key, skipped_by=user.id).on_conflict_do_nothing()
        )
    await db.commit()


async def apply(
    db: AsyncSession, keys: list[str], chosen: dict[str, str] | None = None
) -> tuple[int, list[str], list[str]]:
    """Apply the suggestions named, as they are now. Returns how many were applied, the
    keys no longer offered, and the keys held back: a guess named with others.

    ``chosen`` maps a key to the value the person chose instead of the one offered:
    a category key, for a category suggestion. Any other choice is refused. A guess
    is applied only when it is named alone or a person chose its value (issue 372):
    "Accept these N" never applies one."""
    chosen = chosen or {}
    wanted = set(keys)
    current = {s.key: s for s in await suggestions(db) if s.key in wanted}
    for key, value in chosen.items():
        s = current.get(key)
        if s is not None and not (s.choosable and value in category_keys.KEYS):
            raise ApiError(422, "cleanup_choice", f"{value!r} is not a choice for {s.subject!r}.")
    alone = len(wanted) == 1
    applied, held = 0, []
    for key in keys:
        s = current.get(key)
        if s is None:
            continue
        if key in chosen:
            s = replace(s, value=chosen[key], confidence="sure")
        if not s.ready:
            continue
        if s.confidence == "guess" and not alone:
            held.append(key)
            continue
        await _apply_one(db, s)
        applied += 1
    return applied, [k for k in keys if k not in current], held


async def _apply_one(db: AsyncSession, s: Suggestion) -> None:
    if s.kind == "category":
        await catalog.update_ingredient(db, s.subject_id, IngredientUpdate(category=s.value))
    elif s.kind == "ingredient_name":
        await catalog.update_ingredient(
            db, s.subject_id, IngredientUpdate(name=s.value, keep_name=True)
        )
    elif s.kind == "usda_link":
        if s.value:
            await catalog.set_preferred_fdc(db, s.subject_id, int(s.value))
        else:
            await db.execute(
                update(IngredientRef)
                .where(IngredientRef.ingredient_id == s.subject_id, IngredientRef.is_preferred)
                .values(is_preferred=False)
            )
        await db.commit()
    elif s.kind == "ingredient_merge":
        survivor = await db.get(Ingredient, uuid.UUID(s.value))
        assert survivor is not None  # noqa: S101 - the finder just listed it
        await ingredient_reconcile.merge(db, survivor.id, s.subject_id, name=survivor.name)
    elif s.kind == "product_name":
        await catalog.update_product(db, s.subject_id, ProductUpdate(name=s.value))
    elif s.kind == "product_brand":
        update_ = ProductUpdate(kind=s.value) if s.value else ProductUpdate(brand=None)
        await catalog.update_product(db, s.subject_id, update_)
