"""The category a new ingredient most likely has, so naming one is a confirmation (#344).

Asked by the UI as soon as a new ingredient's name is typed, and shown as a chip
the person can change. Sources, most certain first:

0. ``name``: "frozen" in the name, which files any food with the frozen foods;
1. ``standard``: the standard-list entry the name matches;
2. ``usda``: the food group of the USDA food the person chose for it;
3. ``clip``: the category a captured page or a model reading gave the product;
4. ``name`` again: a word that settles the category on its own (a fish, a spice
   blend, a canned or dried food; ``categories.key_from_name``);
5. ``catalog``: the category the household's ingredients with the same head
   noun mostly have ("smoked paprika" files where "paprika" does);
6. ``usda`` again: the food group of the best USDA food whose description fits
   the name (``usda_fit``).

Nothing is stored here: the person's confirmed choice travels with the create.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import categories
from app.catalog import proposals as merging
from app.catalog.categories import CategoryKey
from app.catalog.names import normalize_name
from app.catalog.standard_match import match_entry
from app.models.catalog import FdcFood, Ingredient
from app.models.proposals import ProductProposal
from app.services import usda

Source = Literal["name", "standard", "usda", "clip", "catalog"]


@dataclass(frozen=True)
class Suggestion:
    category: CategoryKey | None
    source: Source | None


NONE = Suggestion(None, None)


async def suggest(
    db: AsyncSession,
    name: str,
    *,
    fdc_id: int | None = None,
    proposal_id: uuid.UUID | None = None,
) -> Suggestion:
    by_name = categories.key_from_name(name)
    if by_name == "frozen":
        return Suggestion(by_name, "name")
    entry = match_entry(name)
    if entry is not None and (key := categories.key(entry.category)) is not None:
        return Suggestion(key, "standard")
    if (chosen := await _from_choices(db, fdc_id, proposal_id)) is not None:
        return chosen
    if by_name is not None:
        return Suggestion(by_name, "name")
    if (key := await _from_catalog(db, name)) is not None:
        return Suggestion(key, "catalog")
    words = normalize_name(name).split()
    if words:
        for food in await usda.candidates(db, " ".join(words), limit=5, fit_name=name):
            if (key := categories.key(food.category)) is not None:
                return Suggestion(key, "usda")
    return NONE


async def _from_choices(
    db: AsyncSession, fdc_id: int | None, proposal_id: uuid.UUID | None
) -> Suggestion | None:
    """The key of the USDA food chosen for it, else of the captured page's category."""
    if fdc_id is not None:
        food = await db.get(FdcFood, fdc_id)
        if food is not None and (key := categories.key(food.category)) is not None:
            return Suggestion(key, "usda")
    if proposal_id is not None:
        proposal = await db.get(ProductProposal, proposal_id)
        if proposal is not None:
            said = merging.value(proposal.fields or {}, "category")
            if isinstance(said, str) and (key := categories.key_from_text(said)) is not None:
                return Suggestion(key, "clip")
    return None


async def _from_catalog(db: AsyncSession, name: str) -> CategoryKey | None:
    """The key most of the household's ingredients with this head noun have."""
    words = normalize_name(name).split()
    if not words:
        return None
    head = words[-1]
    lowered = func.lower(Ingredient.name)
    rows = await db.execute(
        select(Ingredient.category).where(
            Ingredient.active,
            Ingredient.category.is_not(None),
            or_(lowered == head, lowered.like(f"% {head}")),
        )
    )
    keys = Counter(k for (c,) in rows if (k := categories.key(c)) is not None)
    if not keys:
        return None
    (best, count), *rest = [*keys.most_common(2), (None, 0)]
    # A tie says nothing about where this one belongs.
    return best if rest[0][1] < count else None
