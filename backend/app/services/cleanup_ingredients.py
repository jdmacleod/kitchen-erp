"""Tidy the catalog's ingredient checks: categories and USDA links (#349, issue 372).

Categories: an ingredient without one is offered the likeliest, and one filed
where its name says otherwise (``categories.misfiled``: a frozen food, a fish
under meat, a canned or dried food in a fresh case) is offered where the name
puts it. USDA links: a preferred USDA food that doesn't fit is relinked to the
first that does, or unlinked.

Each suggestion says how sure it is. A category settled by the name, the
standard list or a store page is sure; one guessed from similar ingredients or
from a USDA food group is a guess. A relink to a Foundation record that is raw or
dry is sure; any other relink is a guess, and so is unlinking a record that names
the food in another form. The slow searches are kept per name
(``cleanup_guesses``).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from sqlalchemy import Text, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import categories as category_keys
from app.catalog.categories import CategoryKey
from app.catalog.usda_fit import fits
from app.models.catalog import FdcFood, Ingredient, IngredientRef
from app.services import cleanup_guesses, ingredient_category
from app.services.cleanup_suggestion import Confidence, Suggestion, suggestion_key
from app.services.usda import RAW_WORDS

SURE_SOURCES = frozenset({"name", "standard", "clip"})

_SOURCE_REASONS = {
    "name": "its name says so",
    "standard": "the standard list files it there",
    "usda": "a guess from the USDA food group",
    "clip": "the store page files it there",
    "catalog": "a guess from similar ingredients",
}

_MISFILED_REASONS: dict[CategoryKey, str] = {
    "frozen": "the name says frozen",
    "seafood": "the name says it's a fish or shellfish",
    "pantry": "the name says it's canned, dried, fried or pickled",
    "spices": "the name says it's a seasoning",
}


async def categories(db: AsyncSession, rows: list[Ingredient], *, guess: bool) -> list[Suggestion]:
    out = []
    unfiled = []
    for i in rows:
        if not i.category:
            unfiled.append(i)
        elif (key := category_keys.misfiled(i.name, i.category)) is not None:
            out.append(
                Suggestion(
                    suggestion_key("category", i.id, key),
                    "category",
                    i.id,
                    i.name,
                    i.category,
                    key,
                    _MISFILED_REASONS.get(key, "its name says so"),
                    key,
                )
            )
    found = await _guess(db, unfiled) if guess else {}
    for i in unfiled:
        value, source = found.get(i.name, (None, None))
        reason = _SOURCE_REASONS.get(source, "") if source else ""
        # The key doesn't carry the guess: a skip holds until a category is set.
        out.append(
            Suggestion(
                suggestion_key("category", i.id, ""),
                "category",
                i.id,
                i.name,
                "no category",
                value or "choose one",
                reason or "no likely category: choose one",
                value or "",
                confidence="sure" if source in SURE_SOURCES else "guess",
            )
        )
    return out


async def _guess(
    db: AsyncSession, rows: list[Ingredient]
) -> dict[str, tuple[CategoryKey | None, str | None]]:
    """``ingredient_category.suggest`` for many names at once, in its order: what the
    name settles, then the household's head nouns, then the kept USDA guesses."""
    found: dict[str, tuple[CategoryKey | None, str | None]] = {}
    for i in rows:
        if (words := ingredient_category.from_words(i.name)) is not None:
            found[i.name] = (words.category, words.source)
    heads = {
        i.name: head
        for i in rows
        if i.name not in found and (head := ingredient_category.head_noun(i.name)) is not None
    }
    filed = await _filed_by_head(db, set(heads.values()))
    for name, head in heads.items():
        if (key := ingredient_category.most_filed(filed[head])) is not None:
            found[name] = (key, "catalog")
    rest = {i.name for i in rows if i.name not in found}
    kept = await cleanup_guesses.guesses(db, "category", rest)
    for name, guess in kept.items():
        if (key := category_keys.key(guess.category)) is not None:
            found[name] = (key, "usda")
    return found


async def _filed_by_head(db: AsyncSession, heads: set[str]) -> defaultdict[str, Counter]:
    """For each head noun, the keys of the filed ingredients whose name ends in it."""
    out: defaultdict[str, Counter] = defaultdict(Counter)
    if not heads:
        return out
    rows = await db.execute(
        select(func.lower(Ingredient.name), Ingredient.category).where(
            Ingredient.active, Ingredient.category.is_not(None)
        )
    )
    for lowered, category in rows:
        last = lowered.rsplit(" ", 1)[-1]
        if last in heads and (key := category_keys.key(category)) is not None:
            out[last][key] += 1
    return out


async def usda_links(db: AsyncSession, rows: list[Ingredient], *, guess: bool) -> list[Suggestion]:
    by_id = {i.id: i for i in rows}
    linked = await db.execute(
        select(IngredientRef.ingredient_id, IngredientRef.external_id, FdcFood.description)
        .join(FdcFood, cast(FdcFood.fdc_id, Text) == IngredientRef.external_id)
        .where(IngredientRef.system == "fdc", IngredientRef.is_preferred)
    )
    misfits = []
    for ingredient_id, fdc_id, description in linked:
        ingredient = by_id.get(ingredient_id)
        if ingredient is None:
            continue
        fit = fits(ingredient.name, description)
        if fit.verdict != "fits":
            misfits.append((ingredient, fdc_id, description, fit))
    kept = (
        await cleanup_guesses.guesses(db, "usda_link", {m[0].name for m in misfits})
        if guess
        else {}
    )
    ids = {k.fdc_id for k in kept.values() if k.fdc_id is not None}
    foods = (
        {
            f.fdc_id: f
            for f in (await db.execute(select(FdcFood).where(FdcFood.fdc_id.in_(ids)))).scalars()
        }
        if ids
        else {}
    )
    out = []
    for ingredient, fdc_id, description, fit in misfits:
        found = kept.get(ingredient.name)
        better = foods.get(found.fdc_id) if found and found.fdc_id is not None else None
        value = str(better.fdc_id) if better else ""
        if better is not None:
            confidence = relink_confidence(better)
        else:
            # Unlinking a food that names another is sure; one in another form, a guess.
            confidence = "sure" if fit.verdict == "misfit" else "guess"
        out.append(
            Suggestion(
                suggestion_key("usda_link", ingredient.id, f"{fdc_id}>{value}"),
                "usda_link",
                ingredient.id,
                ingredient.name,
                description,
                better.description if better else "no USDA food",
                fit.reason,
                value,
                confidence=confidence,
            )
        )
    return out


def relink_confidence(food: FdcFood) -> Confidence:
    """Sure for a Foundation record of the food as bought, raw or dry; else a guess."""
    words = set(re.findall(r"[a-z]+", food.description.lower()))
    raw = words.intersection(RAW_WORDS)
    return "sure" if food.data_type == "foundation_food" and raw else "guess"
