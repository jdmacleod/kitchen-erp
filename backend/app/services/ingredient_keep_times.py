"""Catch up ingredient keep times with the standard list and perishability defaults (2Q).

Ingredients made before keep times existed have none. ``plan`` proposes times for
each ingredient, leaving out any place a person has set or a review approved; a person reviews the
proposals, and ``apply`` writes only the times they approved, then recomputes the
inferred best-by dates of the ingredient's purchases. A time is a person's when it
differs from what a source last wrote to it (``field_source``, the 1F edit-wins
rule); a time with no record is proposed, since nobody has reviewed it yet.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import categories, keep, standard
from app.models import Ingredient
from app.services import best_by, interchange


def _field(place: str) -> str:
    return f"keep_{place}_days"


def settled(ingredient: Ingredient, place: str) -> bool:
    """True when a person set this keep time, or approved it in a review."""
    name = _field(place)
    record = (ingredient.field_source or {}).get(name)
    if not isinstance(record, dict):
        return False
    if record.get("source") == "review":
        return True
    return getattr(ingredient, name) != interchange.recorded(ingredient, name)


def proposal(ingredient: Ingredient) -> tuple[keep.KeepTimes, str]:
    """(times, basis): the standard entry's times, else its perishability's defaults."""
    entry = standard.entry(ingredient.slug) if ingredient.slug else None
    if entry is not None:
        return entry.keep, f"standard:{entry.key}"
    value = ingredient.perishability
    return keep.BY_PERISHABILITY[value], f"perishability:{value}"  # type: ignore[index]


async def plan(db: AsyncSession) -> list[dict[str, Any]]:
    """Proposed times for every active ingredient, leaving out places already settled."""
    rows = (
        await db.execute(
            select(Ingredient)
            .where(Ingredient.active.is_(True), Ingredient.merged_into.is_(None))
            .order_by(Ingredient.name)
        )
    ).scalars()
    out = []
    for ingredient in rows:
        open_places = [p for p in keep.PLACES if not settled(ingredient, p)]
        if not open_places:
            continue
        times, basis = proposal(ingredient)
        out.append(
            {
                "id": str(ingredient.id),
                "name": ingredient.name,
                "category": categories.key(ingredient.category),
                "perishability": ingredient.perishability,
                "current": {p: getattr(ingredient, _field(p)) for p in open_places},
                "proposed": {p: times.days(p) for p in open_places},
                "basis": basis,
            }
        )
    return out


async def apply(db: AsyncSession, decisions: list[dict[str, Any]]) -> dict[str, int]:
    """Write each approved ``{id, room?, fridge?, freezer?}``; the times become the review's.

    A place left out of a decision is left alone; null means no time for that place.
    """
    counts = {"changed": 0, "unchanged": 0, "missing": 0}
    now = datetime.now(UTC).isoformat()
    for decision in decisions:
        given = {p: decision[p] for p in keep.PLACES if p in decision}
        for place, days in given.items():
            if days is not None and (type(days) is not int or days < 0):
                raise ValueError(f"not a keep time in days: {place}={days!r}")
        ingredient = await db.get(Ingredient, uuid.UUID(str(decision["id"])))
        if ingredient is None or ingredient.merged_into is not None:
            counts["missing"] += 1
            continue
        changed = any(getattr(ingredient, _field(p)) != d for p, d in given.items())
        counts["changed" if changed else "unchanged"] += 1
        records = dict(ingredient.field_source or {})
        for place, days in given.items():
            setattr(ingredient, _field(place), days)
            records[_field(place)] = {
                "source": "review",
                "ref": None,
                "checked_at": now,
                "imported": days,
            }
        ingredient.field_source = records
        if changed:
            await db.flush()
            await best_by.refresh_ingredient(db, ingredient.id)
    await db.commit()
    return counts
