"""USDA suggestions for linked ingredients, reviewed on Needs a bridge (03 and 10, 1G).

An ingredient with a preferred USDA reference gets that food's portions as
suggestions: densities from volume portions, named measures from the rest.
What the ingredient already has is left out (DV19): a density when it has one,
a measure whose label it has. An ingredient counted in ``each`` never gets a
density (DV21); its measures are counted against USDA's own "medium" or
"each" portion, and it gets none when USDA gives no such portion.

Accepting goes through the same write paths as the ingredient page: values
are stored ``source = usda`` and unconfirmed, and prices are recomputed. The
decision records ``usda_reviewed_fdc_id``, so the ingredient leaves the list
until its reference changes. There is no accept-all (DV18).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

from sqlalchemy import Integer as SqlInteger
from sqlalchemy import cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.models.catalog import FdcFood, Ingredient, IngredientMeasure, IngredientRef, RefUsdaPortion
from app.services import pricebook
from app.services.usda import _UNITS, _classify, current_release, is_loaded

_Q5 = Decimal("0.00001")
_Q3 = Decimal("0.001")
# USDA count portions that stand for one of the ingredient, most specific last.
_ONE_OF = ("each", "whole", "medium")
_LEADING_AMOUNT = re.compile(r"^[\d./\s]+")


def _plain(value: Decimal, places: Decimal) -> Decimal:
    """Rounded, without trailing zeros or an exponent: 3.00000 → 3, 0.500 → 0.5."""
    return Decimal(format(value.quantize(places, rounding=ROUND_HALF_EVEN).normalize(), "f"))


@dataclass
class DensityOffer:
    portion_id: uuid.UUID
    portion_label: str
    gram_weight: Decimal
    density_g_per_ml: Decimal


@dataclass
class MeasureOffer:
    label: str
    canonical_qty: Decimal
    from_portion: str


@dataclass
class ReviewGroup:
    ingredient_id: uuid.UUID
    name: str
    canonical_unit: str
    reconcile_state: str
    fdc_id: int
    usda_description: str | None
    has_density: bool
    densities: list[DensityOffer] = field(default_factory=list)
    measures: list[MeasureOffer] = field(default_factory=list)


@dataclass
class ReviewList:
    loaded: bool
    release_date: date | None
    groups: list[ReviewGroup]


def _offers(
    portions: list[RefUsdaPortion], unit: str, has_density: bool, labels: set[str]
) -> tuple[list[DensityOffer], list[MeasureOffer]]:
    densities: list[DensityOffer] = []
    named: list[tuple[RefUsdaPortion, str, Decimal]] = []
    for p in portions:
        kind, code = _classify(p.portion_unit)
        per_unit_g = p.gram_weight / p.portion_amount
        if kind == "volume" and code is not None:
            if unit != "each" and not has_density:
                densities.append(
                    DensityOffer(
                        portion_id=p.id,
                        portion_label=p.portion_label,
                        gram_weight=p.gram_weight,
                        density_g_per_ml=(per_unit_g / _UNITS[code].to_base_factor).quantize(
                            _Q5, rounding=ROUND_HALF_EVEN
                        ),
                    )
                )
            if unit == "each":
                # "1 cup chopped" → "cup chopped": the measure is one of these.
                named.append((p, _LEADING_AMOUNT.sub("", p.portion_label.lower()), per_unit_g))
        elif kind in {"named", "count"}:
            label = p.portion_unit.lower() if kind == "named" else "each"
            named.append((p, label, per_unit_g))
    measures: list[MeasureOffer] = []
    if unit == "g":
        for p, label, grams in named:
            measures.append(MeasureOffer(label, _plain(grams, _Q5), p.portion_label))
    elif unit == "each":
        # One of the ingredient is USDA's "medium" (or "whole", or "each") portion.
        reference = None
        for word in _ONE_OF:
            reference = next((g for _, lbl, g in named if lbl == word), reference)
        if reference is not None:
            for p, label, grams in named:
                if label in _ONE_OF:
                    continue
                qty = _plain(grams / reference, _Q3)
                if qty > 0:
                    measures.append(MeasureOffer(label, qty, p.portion_label))
    # ml ingredients get densities only: a measure in ml would need the density first.
    seen: set[str] = set()
    unique: list[MeasureOffer] = []
    for m in measures:
        if m.label in labels or m.label in seen:
            continue
        seen.add(m.label)
        unique.append(m)
    return densities, unique


async def _group(db: AsyncSession, ingredient: Ingredient, fdc_id: int) -> ReviewGroup:
    portions = list(
        (
            await db.execute(
                select(RefUsdaPortion)
                .where(RefUsdaPortion.fdc_id == fdc_id)
                .order_by(RefUsdaPortion.portion_label)
            )
        ).scalars()
    )
    labels = {
        label.lower()
        for label in (
            await db.execute(
                select(IngredientMeasure.label).where(
                    IngredientMeasure.ingredient_id == ingredient.id
                )
            )
        ).scalars()
    }
    has_density = ingredient.density_g_per_ml is not None
    densities, measures = _offers(portions, ingredient.canonical_unit, has_density, labels)
    food = await db.get(FdcFood, fdc_id)
    return ReviewGroup(
        ingredient_id=ingredient.id,
        name=ingredient.name,
        canonical_unit=ingredient.canonical_unit,
        reconcile_state=ingredient.reconcile_state,
        fdc_id=fdc_id,
        usda_description=food.description if food else None,
        has_density=has_density,
        densities=densities,
        measures=measures,
    )


async def review_list(db: AsyncSession) -> ReviewList:
    """Linked ingredients with USDA suggestions nobody has decided on yet."""
    if not await is_loaded(db):
        return ReviewList(loaded=False, release_date=None, groups=[])
    release = await current_release(db)
    rows = await db.execute(
        select(Ingredient, cast(IngredientRef.external_id, SqlInteger))
        .join(IngredientRef, IngredientRef.ingredient_id == Ingredient.id)
        .where(
            Ingredient.active,
            IngredientRef.system == "fdc",
            IngredientRef.is_preferred,
            func.coalesce(Ingredient.usda_reviewed_fdc_id, -1)
            != cast(IngredientRef.external_id, SqlInteger),
        )
        .order_by(Ingredient.name)
    )
    groups = []
    for ingredient, fdc_id in rows.tuples():
        group = await _group(db, ingredient, fdc_id)
        if group.densities or group.measures:
            groups.append(group)
    return ReviewList(
        loaded=True, release_date=release.release_date if release else None, groups=groups
    )


async def decide(
    db: AsyncSession,
    ingredient_id: uuid.UUID,
    *,
    density_portion_id: uuid.UUID | None,
    measures: list[str],
    skip: bool,
    replace_density: bool = False,
) -> tuple[int, Ingredient]:
    """Accept the chosen suggestions, or skip them all. Returns (values saved, ingredient)."""
    ingredient = await db.get(Ingredient, ingredient_id)
    if ingredient is None or not ingredient.active:
        raise ApiError(404, "not_found", "No such active ingredient.")
    fdc = (
        await db.execute(
            select(IngredientRef.external_id).where(
                IngredientRef.ingredient_id == ingredient.id,
                IngredientRef.system == "fdc",
                IngredientRef.is_preferred,
            )
        )
    ).scalar_one_or_none()
    if fdc is None:
        raise ApiError(409, "no_usda_reference", "This ingredient has no USDA reference.")
    fdc_id = int(fdc)
    saved = 0
    if not skip:
        raced = density_portion_id is not None and ingredient.density_g_per_ml is not None
        if raced and not replace_density:
            raise ApiError(
                409,
                "density_exists",
                f"{ingredient.name} now has a density.",
                details={
                    "density_g_per_ml": format(ingredient.density_g_per_ml, "f"),
                    "density_source": ingredient.density_source,
                    "density_confirmed": ingredient.density_confirmed,
                },
            )
        # Offers as if there were no density yet, so a Replace finds its portion.
        group = await _group(db, ingredient, fdc_id)
        densities, _ = _offers(
            list(
                (
                    await db.execute(select(RefUsdaPortion).where(RefUsdaPortion.fdc_id == fdc_id))
                ).scalars()
            ),
            ingredient.canonical_unit,
            False,
            set(),
        )
        if density_portion_id is not None:
            offer = next((d for d in densities if d.portion_id == density_portion_id), None)
            if offer is None:
                raise ApiError(422, "unknown_portion", "That USDA portion isn't a density offer.")
            ingredient.density_g_per_ml = offer.density_g_per_ml
            ingredient.density_source = "usda"
            ingredient.density_confirmed = False
            saved += 1
        offered = {m.label: m for m in group.measures}
        for label in measures:
            m = offered.get(label.lower())
            if m is None:
                continue  # taken since the list was read, or never offered
            db.add(
                IngredientMeasure(
                    ingredient_id=ingredient.id,
                    label=m.label,
                    canonical_qty=m.canonical_qty,
                    source="usda",
                    confirmed=False,
                )
            )
            saved += 1
    ingredient.usda_reviewed_fdc_id = fdc_id
    await db.commit()
    if saved:
        await pricebook.recompute_for_ingredient(db, ingredient.id)
    await db.refresh(ingredient)
    return saved, ingredient
