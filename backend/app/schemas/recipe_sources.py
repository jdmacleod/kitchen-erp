"""What a recipe makes and which recipes cost from which (07, 3D; #526).

Kept apart from ``schemas/recipes`` so the ingredient's schemas (``catalog``),
which that module imports, can use them too.
"""

from __future__ import annotations

import uuid
from typing import Literal

from app.schemas.base import ApiModel, DecimalStr

RecipeStatus = Literal["ok", "parse_error", "missing"]


class RecipeLink(ApiModel):
    id: uuid.UUID
    title: str
    path: str
    status: RecipeStatus = "ok"


class MakesOut(ApiModel):
    """Front matter `yield:` as read: the text as written, and when it is an amount
    and a mass or volume unit, the amount, its unit, and the same in the
    household's display unit ("16.9" "fl oz" for 500 ml)."""

    text: str | None
    qty: DecimalStr | None = None
    unit: str | None = None
    display_qty: DecimalStr | None = None
    display_unit: str | None = None


class MakesUnitCost(ApiModel):
    """A recipe's counted cost per display unit of what it makes ("$0.62 per fl oz").

    ``partial`` when that cost leaves lines out; ``source_basis`` is "latest" when
    a kitchen's view or the offers basis read the stored latest cost instead.
    """

    amount: DecimalStr
    unit: str
    partial: bool
    source_basis: str | None = None


class MadeByRecipeOut(RecipeLink):
    """The recipe an ingredient is made by, with what it makes and costs per unit."""

    makes_text: str | None
    makes_ok: bool
    unit_cost: MakesUnitCost | None = None


class LinkWarning(ApiModel):
    """A link was made, with something to say: ``recipe_cycle`` names the chain of
    titles that now goes round (the link is kept; those lines read ``cycle``)."""

    code: Literal["recipe_cycle"]
    message: str
    chain: list[RecipeLink] = []  # noqa: RUF012
