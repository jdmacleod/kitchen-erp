"""USDA's portion for a recipe line that can't convert, offered on the line itself.

A line fails to convert when its ingredient lacks a bridge: a density for "0.5 tsp
black pepper", the weight of one for "2 onions", a measure for "3 cloves". When the
ingredient has a preferred USDA reference, the USDA review (``usda_review``) already
knows what that food's portions offer; this picks the one offer that would convert
the line, so the cost table can say "USDA: 1 medium = 110 g · Use it".

Nothing is applied here. "Use it" goes through ``usda_review.decide`` like the
review on Needs a bridge: the value is stored ``source = usda``, unconfirmed, and
prices and recipe costs are recomputed (non-negotiable 8: a person accepts it).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Integer as SqlInteger
from sqlalchemy import cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Ingredient, RecipeCostLine, RecipeIngredient
from app.models.catalog import IngredientRef
from app.services import usda_review
from app.services.recipe_costing import from_unit_of
from app.services.usda_review import DensityOffer, MeasureOffer, ReviewGroup, measure_label
from app.units import UnitParseFailure, parse_unit
from app.units.convert import PIECE_LABELS
from app.units.table import units_by_code

_LEADING_AMOUNT = re.compile(r"^[\d./\s]+")
# The failures a USDA portion can mend; a missing price or mapping it can't.
_MENDABLE = frozenset({"no_density", "unknown_measure"})


@dataclass(frozen=True)
class LineOffer:
    """One USDA value that would convert the line: a density or a named measure."""

    ingredient_id: uuid.UUID
    kind: str  # "density" or "measure"
    density_portion_id: uuid.UUID | None
    measure_label: str | None
    text: str  # "1 tsp = 2.3 g", "1 medium = 110 g"


def _plain(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _density(group: ReviewGroup, from_unit: str) -> LineOffer | None:
    """The portion in the line's own unit when USDA has one, else the review's choice."""
    if not group.densities:
        return None

    def in_unit(d: DensityOffer) -> bool:
        # "1 tablespoon" is the line's "tbsp"; "1 cup, chopped" is its "cup".
        words = _LEADING_AMOUNT.sub("", d.portion_label.lower()).split(",", 1)[0].strip()
        parsed = parse_unit(words)
        return not isinstance(parsed, UnitParseFailure) and parsed == from_unit

    pick = (
        next((d for d in group.densities if in_unit(d)), None)
        or next((d for d in group.densities if d.suggested), None)
        or group.densities[0]
    )
    return LineOffer(
        group.ingredient_id,
        "density",
        pick.portion_id,
        None,
        f"{pick.portion_label} = {_plain(pick.gram_weight)} g",
    )


def _measure(group: ReviewGroup, from_unit: str) -> LineOffer | None:
    """The measure the line's unit names, or for a bare count the measure for one."""
    by_label = {m.label: m for m in group.measures}
    wanted = PIECE_LABELS if from_unit.lower() == "each" else (measure_label(from_unit),)
    found: MeasureOffer | None = next((by_label[w] for w in wanted if w in by_label), None)
    if found is None:
        return None
    return LineOffer(
        group.ingredient_id,
        "measure",
        None,
        found.label,
        f"1 {found.label} = {_plain(found.canonical_qty)} {group.canonical_unit}",
    )


def _offer(group: ReviewGroup, failure: str, from_unit: str) -> LineOffer | None:
    unit = units_by_code().get(from_unit)
    if failure == "no_density" and unit is not None and unit.dimension == "volume":
        return _density(group, from_unit)
    if failure == "unknown_measure":
        return _measure(group, from_unit)
    return None


async def offers_for(
    db: AsyncSession, rows: list[tuple[RecipeCostLine, RecipeIngredient]]
) -> dict[uuid.UUID, LineOffer]:
    """Each unconvertible line's USDA offer, by cost line id; lines with none are left out."""
    wanting = [
        (line, ri)
        for line, ri in rows
        if line.status == "unconvertible"
        and line.failure_code in _MENDABLE
        and ri.ingredient_id is not None
    ]
    if not wanting:
        return {}
    ingredient_ids = {ri.ingredient_id for _, ri in wanting}
    pairs = list(
        (
            await db.execute(
                select(Ingredient, cast(IngredientRef.external_id, SqlInteger))
                .join(IngredientRef, IngredientRef.ingredient_id == Ingredient.id)
                .where(
                    Ingredient.id.in_(ingredient_ids),
                    Ingredient.active,
                    IngredientRef.system == "fdc",
                    IngredientRef.is_preferred,
                )
            )
        ).tuples()
    )
    if not pairs:
        return {}
    groups = {g.ingredient_id: g for g in await usda_review.groups_for(db, pairs)}
    out: dict[uuid.UUID, LineOffer] = {}
    for line, ri in wanting:
        group = groups.get(ri.ingredient_id) if ri.ingredient_id else None
        if group is None:
            continue
        offer = _offer(group, line.failure_code or "", from_unit_of(ri))
        if offer is not None:
            out[line.id] = offer
    return out
