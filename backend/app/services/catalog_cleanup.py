"""Tidy the catalog: fixes proposed in batches, applied only on a person's accept (#349).

The checks that keep new products and ingredients tidy (#342–#348) also find
what the catalog already holds that they would have prevented. Each finding is a
suggestion with a stable key: the kind, the row and what it proposes, so a skip
sticks until the data changes. Kinds:

- ``category``: an ingredient without a category, with the likeliest one;
- ``ingredient_name``: an ingredient name the 1G rules write differently;
- ``usda_link``: a preferred USDA reference that doesn't fit, relinked to one
  that does or unlinked;
- ``ingredient_merge``: two ingredients that look like one food;
- ``product_name``: a product name that repeats its brand or pack;
- ``product_brand``: a brand that only says "none", or a brand on a loose product.

Accepting calls the existing services; nothing is written here otherwise.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import Text, cast, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import proposals as merging
from app.catalog.conform import conform, proper_noun_words
from app.catalog.product_names import short_name
from app.catalog.usda_fit import fits
from app.models import AppUser
from app.models.catalog import CleanupSkip, FdcFood, Ingredient, IngredientRef, Product
from app.schemas.catalog import IngredientUpdate, ProductUpdate
from app.services import (
    catalog,
    ingredient_category,
    ingredient_duplicates,
    ingredient_reconcile,
    usda,
)
from app.services.ingredient_names import brand_keys

Kind = Literal[
    "category", "ingredient_name", "usda_link", "ingredient_merge", "product_name", "product_brand"
]
KINDS: tuple[Kind, ...] = (
    "ingredient_merge",
    "usda_link",
    "category",
    "ingredient_name",
    "product_name",
    "product_brand",
)


@dataclass(frozen=True)
class Suggestion:
    key: str
    kind: Kind
    subject_id: uuid.UUID
    subject: str
    before: str
    after: str
    reason: str
    # What accepting applies: a category, a name, a USDA id ("" unlinks), a
    # survivor id, a kind, or "" to clear a brand.
    value: str
    other_id: uuid.UUID | None = None

    @property
    def ready(self) -> bool:
        """Whether accepting it can apply something; a category nobody could guess can't."""
        return self.kind != "category" or bool(self.value)


def _key(kind: str, subject: uuid.UUID, value: str) -> str:
    digest = hashlib.sha1(value.encode(), usedforsecurity=False).hexdigest()[:10]
    return f"{kind}:{subject}:{digest}"


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


async def _categories(db: AsyncSession, rows: list[Ingredient], *, guess: bool) -> list[Suggestion]:
    out = []
    for i in rows:
        if i.category:
            continue
        found = await ingredient_category.suggest(db, i.name) if guess else None
        value = found.category if found and found.category else ""
        reason = f"suggested {found.source}" if found and found.source else "no likely category"
        # The key doesn't carry the guess: a skip holds until a category is set.
        out.append(
            Suggestion(
                _key("category", i.id, ""),
                "category",
                i.id,
                i.name,
                "no category",
                value or "choose one",
                reason,
                value,
            )
        )
    return out


def _names(rows: list[Ingredient], keys: set[str]) -> list[Suggestion]:
    out = []
    for i in rows:
        found = conform(i.name, brand_keys=keys)
        if found.name and found.name != i.name:
            out.append(
                Suggestion(
                    _key("ingredient_name", i.id, found.name),
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


async def _usda_links(db: AsyncSession, rows: list[Ingredient], *, guess: bool) -> list[Suggestion]:
    by_id = {i.id: i for i in rows}
    out = []
    linked = await db.execute(
        select(IngredientRef.ingredient_id, IngredientRef.external_id, FdcFood.description)
        .join(FdcFood, cast(FdcFood.fdc_id, Text) == IngredientRef.external_id)
        .where(IngredientRef.system == "fdc", IngredientRef.is_preferred)
    )
    for ingredient_id, fdc_id, description in linked:
        ingredient = by_id.get(ingredient_id)
        if ingredient is None:
            continue
        fit = fits(ingredient.name, description)
        if fit.verdict == "fits":
            continue
        better = None
        if guess:
            found = await usda.candidates(db, ingredient.name, limit=5, fit_name=ingredient.name)
            better = next(
                (f for f in found if fits(ingredient.name, f.description).verdict == "fits"), None
            )
        value = str(better.fdc_id) if better else ""
        after = better.description if better else "no USDA food"
        out.append(
            Suggestion(
                _key("usda_link", ingredient_id, f"{fdc_id}>{value}"),
                "usda_link",
                ingredient_id,
                ingredient.name,
                description,
                after,
                fit.reason,
                value,
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
                _key("ingredient_merge", other.id, str(keep.id)),
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
        )
        .where(Product.active)
        .order_by(Product.name)
    )
    for p in rows:
        if p.brand and merging.is_null_like(p.brand):
            out.append(
                Suggestion(
                    _key("product_brand", p.id, ""),
                    "product_brand",
                    p.id,
                    p.name,
                    f"brand “{p.brand}”",
                    "no brand",
                    "the brand only says none",
                    "",
                )
            )
        elif p.brand and p.kind in ("loose", "random_weight"):
            # A brand the dataset knows is linked (brand_id), and is some store's own.
            kind = "private_label" if p.brand_id is not None else "branded"
            out.append(
                Suggestion(
                    _key("product_brand", p.id, kind),
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
            p.name, brand, has_pack=p.pack_qty is not None, brand_keys=keys, proper_nouns=proper
        )
        if short and short != p.name:
            out.append(
                Suggestion(
                    _key("product_name", p.id, short),
                    "product_name",
                    p.id,
                    p.name,
                    p.name,
                    short,
                    "brand and pack have fields of their own",
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
        *await _usda_links(db, rows, guess=guess),
        *await _categories(db, rows, guess=guess),
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


async def apply(db: AsyncSession, keys: list[str]) -> tuple[int, list[str]]:
    """Apply the suggestions named, as they are now; a key no longer offered is reported."""
    wanted = set(keys)
    current = {s.key: s for s in await suggestions(db) if s.key in wanted}
    applied = 0
    for key in keys:
        s = current.get(key)
        if s is None or not s.ready:
            continue
        await _apply_one(db, s)
        applied += 1
    return applied, [k for k in keys if k not in current]


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
