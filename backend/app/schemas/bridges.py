"""Unconfirmed bridges (issue 356)."""

from __future__ import annotations

import uuid
from typing import Literal

from app.schemas.base import ApiModel, DecimalStr
from app.schemas.catalog import CanonicalUnit


class UnconfirmedValueOut(ApiModel):
    kind: Literal["density", "measure", "product_density"]
    # The ingredient for a density, the measure for a measure, the product for an override.
    id: uuid.UUID
    ingredient_id: uuid.UUID
    ingredient_name: str
    canonical_unit: CanonicalUnit
    product_name: str | None
    label: str | None
    # g/ml for a density; a measure's quantity in the ingredient's unit.
    value: DecimalStr
    source: str | None
    # The density a volume measure implies, to three significant figures.
    g_per_ml: DecimalStr | None
    # Why no food could have this value; such values come first.
    problem: str | None


class UnconfirmedList(ApiModel):
    items: list[UnconfirmedValueOut]
