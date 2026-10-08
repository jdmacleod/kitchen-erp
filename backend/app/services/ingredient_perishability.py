"""Catch up ingredient perishability with the standard list and category defaults.

Ingredients made before the standard list carried perishability all hold the old
default, ``shelf_stable``. ``plan`` proposes a value for each ingredient a person
has not set or approved; a person reviews the proposals, and ``apply`` writes only the
values they approved. A field is a person's when it differs from what a source
last wrote to it (``field_source``, the 1F edit-wins rule); an ingredient with no
record at all is proposed too, since nobody has reviewed it yet.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, get_args

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import categories, perishability, standard
from app.models import Ingredient
from app.services import interchange


def _settled(ingredient: Ingredient) -> bool:
    """True when a person set this perishability, or approved it in a review."""
    record = (ingredient.field_source or {}).get("perishability")
    if not isinstance(record, dict):
        return False
    if record.get("source") == "review":
        return True
    return ingredient.perishability != interchange.recorded(ingredient, "perishability")


def proposal(ingredient: Ingredient) -> tuple[str, str]:
    """(value, basis): the standard entry's value, else the category default."""
    entry = standard.entry(ingredient.slug) if ingredient.slug else None
    if entry is not None:
        return entry.perishability, f"standard:{entry.key}"
    key = categories.key(ingredient.category)
    return perishability.default_for(ingredient.category), f"category:{key or 'none'}"


async def plan(db: AsyncSession) -> list[dict[str, Any]]:
    """A proposal for every active ingredient whose perishability nobody has settled."""
    rows = (
        await db.execute(
            select(Ingredient)
            .where(Ingredient.active.is_(True), Ingredient.merged_into.is_(None))
            .order_by(Ingredient.name)
        )
    ).scalars()
    out = []
    for ingredient in rows:
        if _settled(ingredient):
            continue
        value, basis = proposal(ingredient)
        out.append(
            {
                "id": str(ingredient.id),
                "name": ingredient.name,
                "category": categories.key(ingredient.category),
                "current": ingredient.perishability,
                "proposed": value,
                "basis": basis,
            }
        )
    return out


async def apply(db: AsyncSession, decisions: list[dict[str, Any]]) -> dict[str, int]:
    """Write each approved ``{id, perishability}``; the value becomes the review's."""
    allowed = set(get_args(perishability.Perishability))
    counts = {"changed": 0, "unchanged": 0, "missing": 0}
    now = datetime.now(UTC).isoformat()
    for decision in decisions:
        value = decision["perishability"]
        if value not in allowed:
            raise ValueError(f"not a perishability: {value!r}")
        ingredient = await db.get(Ingredient, uuid.UUID(str(decision["id"])))
        if ingredient is None or ingredient.merged_into is not None:
            counts["missing"] += 1
            continue
        counts["changed" if ingredient.perishability != value else "unchanged"] += 1
        ingredient.perishability = value
        ingredient.field_source = {
            **(ingredient.field_source or {}),
            "perishability": {
                "source": "review",
                "ref": None,
                "checked_at": now,
                "imported": value,
            },
        }
    await db.commit()
    return counts
