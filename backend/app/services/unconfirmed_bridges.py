"""Unconfirmed bridges: every density and measure nobody has checked yet (issue 356).

USDA suggestions, model readings and quick entries save their values as
unconfirmed. This lists them in one place: an ingredient's density, its named
measures, and a product's density override. Values no food could have
(``catalog.bridge_check``) come first, with the reason.

Confirming, editing and deleting use the existing write paths; this module
only reads. ``waiting`` is three counts, so the inbox row stays cheap however
long the list is (#327).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.bridge_check import density_problem, read_measure, sig
from app.models.catalog import Ingredient, IngredientMeasure, Product
from app.units import units_by_code

Kind = Literal["density", "measure", "product_density"]
_ORDER: dict[str, int] = {"density": 0, "measure": 1, "product_density": 2}


@dataclass
class UnconfirmedValue:
    kind: Kind
    # The ingredient for a density, the measure for a measure, the product for an override.
    id: uuid.UUID
    ingredient_id: uuid.UUID
    ingredient_name: str
    canonical_unit: str
    product_name: str | None
    label: str | None
    # g/ml for a density; the measure's quantity in the ingredient's unit.
    value: Decimal
    source: str | None
    # The density a volume measure implies, to three significant figures.
    g_per_ml: Decimal | None
    problem: str | None


def _density(i: Ingredient) -> UnconfirmedValue:
    assert i.density_g_per_ml is not None  # noqa: S101 - the query asked for one
    return UnconfirmedValue(
        "density",
        i.id,
        i.id,
        i.name,
        i.canonical_unit,
        None,
        None,
        i.density_g_per_ml,
        i.density_source,
        sig(i.density_g_per_ml, 3),
        density_problem(i.density_g_per_ml),
    )


async def unconfirmed(db: AsyncSession) -> list[UnconfirmedValue]:
    """Every unconfirmed value on an active ingredient or product, implausible ones first."""
    units = units_by_code()
    out = [
        _density(i)
        for i in (
            await db.execute(
                select(Ingredient).where(
                    Ingredient.active,
                    Ingredient.density_g_per_ml.is_not(None),
                    Ingredient.density_confirmed.is_(False),
                )
            )
        ).scalars()
    ]
    measures = await db.execute(
        select(IngredientMeasure, Ingredient)
        .join(Ingredient, Ingredient.id == IngredientMeasure.ingredient_id)
        .where(Ingredient.active, IngredientMeasure.confirmed.is_(False))
    )
    for m, i in measures.tuples():
        reading = read_measure(m.label, m.canonical_qty, i.canonical_unit, units)
        out.append(
            UnconfirmedValue(
                "measure",
                m.id,
                i.id,
                i.name,
                i.canonical_unit,
                None,
                m.label,
                m.canonical_qty,
                m.source,
                reading.g_per_ml,
                reading.problem,
            )
        )
    overrides = await db.execute(
        select(Product, Ingredient)
        .join(Ingredient, Ingredient.id == Product.ingredient_id)
        .where(
            Product.active,
            Product.density_override.is_not(None),
            Product.density_override_confirmed.is_(False),
        )
    )
    for p, i in overrides.tuples():
        assert p.density_override is not None  # noqa: S101 - the query asked for one
        out.append(
            UnconfirmedValue(
                "product_density",
                p.id,
                i.id,
                i.name,
                i.canonical_unit,
                p.name,
                None,
                p.density_override,
                p.density_override_source,
                sig(p.density_override, 3),
                density_problem(p.density_override),
            )
        )
    out.sort(
        key=lambda v: (
            v.problem is None,
            v.ingredient_name.lower(),
            _ORDER[v.kind],
            (v.label or v.product_name or "").lower(),
        )
    )
    return out


async def waiting(db: AsyncSession) -> tuple[int, datetime | None]:
    """How many values wait for a check, and the oldest ingredient among them,
    without reading them: three aggregate queries."""
    oldest = func.min(Ingredient.created_at)
    densities = select(func.count(), oldest).where(
        Ingredient.active,
        Ingredient.density_g_per_ml.is_not(None),
        Ingredient.density_confirmed.is_(False),
    )
    measures = (
        select(func.count(), oldest)
        .select_from(IngredientMeasure)
        .join(Ingredient, Ingredient.id == IngredientMeasure.ingredient_id)
        .where(Ingredient.active, IngredientMeasure.confirmed.is_(False))
    )
    overrides = (
        select(func.count(), oldest)
        .select_from(Product)
        .join(Ingredient, Ingredient.id == Product.ingredient_id)
        .where(
            Product.active,
            Product.density_override.is_not(None),
            Product.density_override_confirmed.is_(False),
        )
    )
    n, first = 0, None
    for q in (densities, measures, overrides):
        found, at = (await db.execute(q)).one()
        n += found
        if at is not None and (first is None or at < first):
            first = at
    return n, first
