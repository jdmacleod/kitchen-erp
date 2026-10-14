"""Possible duplicate ingredients (#346): pairs that look like one food entered twice.

Products have a duplicate finder (2P); ingredients only met theirs when two
claimed the same standard entry on the link page. Here a pair of active
ingredients is offered when any of these hold, each kept as a reason:

- ``same USDA food``: they share a preferred USDA reference that fits both names
  (``usda_fit``), so a wrong link never pairs two foods;
- ``same standard entry``: the standard matcher gives both the same entry;
- ``same name``: conformed (``catalog.conform``) and with the last word's plural
  and spelling variants folded, they read the same ("okra, frozen" and "frozen
  okra"; "red chilies" and "red chiles");
- ``similar name``: a spelling slip, one word each differing and those two words
  nearly alike; "red onion" is a variety of "onion" and "red wine vinegar" a
  different food from "white wine vinegar", so neither pair is offered.

A pair a person marked "Not the same" is remembered in
``ingredient_distinct_pair`` and never offered again. Nothing is merged here:
the link page's merge (``ingredient_reconcile.merge``) does that, with a person.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher

from sqlalchemy import Text, cast, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.conform import conform
from app.catalog.names import normalize_name, singulars, spellings
from app.catalog.standard_match import match_entry
from app.catalog.usda_fit import fits
from app.core.errors import ApiError
from app.models import AppUser
from app.models.catalog import (
    FdcFood,
    Ingredient,
    IngredientDistinctPair,
    IngredientRef,
    Product,
)

# How alike two names ending in the same noun must read to be "similar name".
SIMILARITY = 0.8


@dataclass
class Side:
    id: uuid.UUID
    name: str
    products: int
    standard: bool
    created_at: datetime


@dataclass
class DuplicatePair:
    a: Side
    b: Side
    reasons: list[str] = field(default_factory=list)

    @property
    def keep(self) -> Side:
        """The likelier survivor: the one linked to the standard list, else the busier."""
        return max(
            (self.a, self.b), key=lambda s: (s.standard, s.products, s.name == s.name.lower())
        )


def name_key(name: str) -> str:
    """A name with its conformed form, last word's plural and spelling folded."""
    words = normalize_name(conform(name).name).split()
    if not words:
        return ""
    return " ".join([*words[:-1], _folded(words[-1])])


def _folded(word: str) -> str:
    """One spelling for a last word's plural and spelling variants: "chilies",
    "chillies" and "chili" all give "chile", "tomatoes" gives "tomato"."""
    singular = singulars(word)
    for reading in [*singular, word]:
        group = spellings(reading)
        if len(group) > 1:
            return min(group)
    return singular[0] if singular else word


def _similar(a: str, b: str) -> bool:
    """A spelling slip: one word each that differs, and those two read nearly alike
    ("worcestershire" and "worchestershire"). A different word is a different food
    ("red wine vinegar", "white wine vinegar"), and added words make a variety."""
    wa, wb = set(name_key(a).split()), set(name_key(b).split())
    only_a, only_b = wa - wb, wb - wa
    if len(only_a) != 1 or len(only_b) != 1:
        return False
    return SequenceMatcher(None, only_a.pop(), only_b.pop()).ratio() >= SIMILARITY


def _head(name: str) -> str:
    words = name_key(name).split()
    return words[-1] if words else ""


Pairs = dict[tuple[uuid.UUID, uuid.UUID], list[str]]


def _pair_up(found: Pairs, groups: dict[str, list[uuid.UUID]], reason: str) -> None:
    """Every two ingredients in one group, with the group's reason."""
    for ids in groups.values():
        ordered = sorted(ids)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1 :]:
                if reason not in found[(a, b)]:
                    found[(a, b)].append(reason)


async def _sides(db: AsyncSession) -> dict[uuid.UUID, Side]:
    rows = (
        await db.execute(
            select(
                Ingredient.id, Ingredient.name, Ingredient.reconcile_state, Ingredient.created_at
            ).where(Ingredient.active, Ingredient.merged_into.is_(None))
        )
    ).all()
    counts = dict(
        (
            await db.execute(
                select(Product.ingredient_id, func.count())
                .where(Product.active)
                .group_by(Product.ingredient_id)
            )
        ).all()
    )
    return {
        r.id: Side(r.id, r.name, counts.get(r.id, 0), r.reconcile_state == "linked", r.created_at)
        for r in rows
    }


async def _by_usda_food(db: AsyncSession, sides: dict[uuid.UUID, Side]) -> dict[str, list]:
    """Ingredients by preferred USDA food, counting only a food that fits the name."""
    groups: dict[str, list[uuid.UUID]] = defaultdict(list)
    for ingredient_id, fdc, description in await db.execute(
        select(IngredientRef.ingredient_id, IngredientRef.external_id, FdcFood.description)
        .join(FdcFood, cast(FdcFood.fdc_id, Text) == IngredientRef.external_id)
        .where(IngredientRef.system == "fdc", IngredientRef.is_preferred)
    ):
        side = sides.get(ingredient_id)
        if side is not None and fits(side.name, description).verdict == "fits":
            groups[fdc].append(ingredient_id)
    return groups


def _similar_names(found: Pairs, sides: dict[uuid.UUID, Side]) -> None:
    """Spelling slips among ingredients ending in the same noun, not already paired."""
    by_head: dict[str, list[uuid.UUID]] = defaultdict(list)
    for side in sides.values():
        by_head[_head(side.name)].append(side.id)
    for ids in by_head.values():
        ordered = sorted(ids)
        for i, a in enumerate(ordered):
            for b in ordered[i + 1 :]:
                if not found[(a, b)] and _similar(sides[a].name, sides[b].name):
                    found[(a, b)].append("similar name")


async def find_duplicates(db: AsyncSession) -> list[DuplicatePair]:
    """Every pair of active ingredients that looks like one food entered twice."""
    sides = await _sides(db)
    if len(sides) < 2:
        return []
    found: Pairs = defaultdict(list)
    _pair_up(found, await _by_usda_food(db, sides), "same USDA food")
    by_entry: dict[str, list[uuid.UUID]] = defaultdict(list)
    by_name: dict[str, list[uuid.UUID]] = defaultdict(list)
    for side in sides.values():
        if (entry := match_entry(side.name)) is not None:
            by_entry[entry.key].append(side.id)
        if key := name_key(side.name):
            by_name[key].append(side.id)
    _pair_up(found, by_entry, "same standard entry")
    _pair_up(found, by_name, "same name")
    _similar_names(found, sides)
    distinct = set(
        (
            await db.execute(
                select(IngredientDistinctPair.ingredient_a, IngredientDistinctPair.ingredient_b)
            )
        ).all()
    )
    out = [
        DuplicatePair(sides[a], sides[b], reasons)
        for (a, b), reasons in found.items()
        if reasons and (a, b) not in distinct
    ]
    out.sort(key=lambda p: (p.a.name.lower(), p.b.name.lower()))
    return out


async def mark_distinct(
    db: AsyncSession, user: AppUser, first: uuid.UUID, second: uuid.UUID
) -> None:
    """Remember that two ingredients are not the same; either order names the pair."""
    if first == second:
        raise ApiError(422, "same_ingredient", "Choose two different ingredients.")
    found = (
        await db.execute(select(Ingredient.id).where(Ingredient.id.in_([first, second])))
    ).all()
    if len(found) != 2:
        raise ApiError(404, "not_found", "No such ingredient.")
    a, b = sorted((first, second))
    await db.execute(
        insert(IngredientDistinctPair)
        .values(ingredient_a=a, ingredient_b=b, decided_by=user.id)
        .on_conflict_do_nothing()
    )
    await db.commit()
