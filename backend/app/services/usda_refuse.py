"""USDA refuse: the inedible share of a food as bought, and the yield it implies.

FoodData Central's ``food_component.csv`` lists the parts of a food ("a
constituent part of a food, e.g. bone is a component of meat"), each with
``pct_weight`` ("the weight of the component as a percentage of the total
weight of the food") and ``is_refuse`` ("whether the component is refuse, i.e.
not edible"); USDA's Download Field Descriptions. A food's refuse is the sum of
``pct_weight`` over its refuse rows, and the ingredient form offers
``100 − refuse`` as the yield a person may accept (07, 3D; issue 282). Nothing
here writes an ingredient.
"""

from __future__ import annotations

import csv
import uuid
from collections import Counter
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import Text, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catalog import FdcFood, IngredientRef

FILE = "food_component.csv"
COLUMNS = ("fdc_id", "pct_weight", "is_refuse")
# USDA documents the flag's meaning, not its spelling; these are the spellings
# read. Any other value is counted and its row left out, never guessed at.
_TRUE = {"y", "yes", "true", "t", "1"}
_FALSE = {"n", "no", "false", "f", "0"}
_HUNDRED = Decimal(100)
_YIELD_PLACES = Decimal("0.0001")  # ingredient.yield_pct is NUMERIC(5, 4)
_REFUSE_PLACES = Decimal("0.001")  # fdc_food.refuse_pct is NUMERIC(6, 3)


def _percent(value: str | None) -> Decimal | None:
    try:
        d = Decimal((value or "").strip())
    except InvalidOperation:
        return None
    return d if d.is_finite() and d >= 0 else None


def _flag(value: str | None) -> bool | None:
    v = (value or "").strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    return None


def read_refuse(
    directory: Path, kept: set[int] | dict[int, object], skipped: Counter[str]
) -> dict[int, Decimal]:
    """Refuse percentages of the kept foods that have a refuse component.

    An absent file gives nothing. A file without one of the needed columns is
    not read at all, and ``skipped`` says which column it lacks, so a changed
    USDA layout leaves every food without a refuse figure rather than a wrong
    one. A food whose refuse adds up to 100% or more is left out.
    """
    path = directory / FILE
    if not path.is_file():
        return {}
    totals: dict[int, Decimal] = {}
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        header = [h.strip().lower() for h in next(reader, [])]
        missing = next((c for c in COLUMNS if c not in header), None)
        if missing is not None:
            skipped[f"{FILE} has no column “{missing}”; no refuse figures read"] += 1
            return {}
        for values in reader:
            row = dict(zip(header, values, strict=False))
            raw_id = (row.get("fdc_id") or "").strip()
            if not raw_id.isdigit() or int(raw_id) not in kept:
                continue
            refuse = _flag(row.get("is_refuse"))
            if refuse is None:
                skipped["food component's refuse flag is not yes or no"] += 1
                continue
            if not refuse:
                continue
            pct = _percent(row.get("pct_weight"))
            if pct is None:
                skipped["refuse component has no usable percentage"] += 1
                continue
            totals[int(raw_id)] = totals.get(int(raw_id), Decimal(0)) + pct
    kept_totals: dict[int, Decimal] = {}
    for food, total in totals.items():
        rounded = total.quantize(_REFUSE_PLACES, rounding=ROUND_HALF_EVEN)
        if rounded >= _HUNDRED:
            skipped["refuse adds up to 100% or more"] += 1
        else:
            kept_totals[food] = rounded
    return kept_totals


def yield_from_refuse(refuse_pct: Decimal) -> Decimal:
    """The yield fraction a refuse percentage implies: 12.5 → 0.875."""
    return ((_HUNDRED - refuse_pct) / _HUNDRED).quantize(_YIELD_PLACES, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True)
class UsdaReference:
    fdc_id: int
    description: str
    refuse_pct: Decimal | None
    yield_pct: Decimal | None


async def preferred_reference(db: AsyncSession, ingredient_id: uuid.UUID) -> UsdaReference | None:
    """The ingredient's preferred USDA food with its refuse, if it is in the loaded release."""
    row = (
        await db.execute(
            select(FdcFood)
            .join(IngredientRef, cast(FdcFood.fdc_id, Text) == IngredientRef.external_id)
            .where(
                IngredientRef.ingredient_id == ingredient_id,
                IngredientRef.system == "fdc",
                IngredientRef.is_preferred,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    refuse = row.refuse_pct
    return UsdaReference(
        fdc_id=row.fdc_id,
        description=row.description,
        refuse_pct=refuse,
        yield_pct=None if refuse is None else yield_from_refuse(refuse),
    )
