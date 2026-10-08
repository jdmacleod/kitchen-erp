"""How fast an ingredient spoils where it is normally kept (docs/spec/02-data-model.md).

The four values follow the USDA storage charts (FSIS "Food Product Dating", the
FoodSafety.gov cold storage chart) and the food bank guides that reprint them:

- ``shelf_stable``: keeps months or more at room temperature, unopened;
- ``refrigerated``: kept cold, or lasts weeks, about a week or longer;
- ``fresh``: lasts a week or less;
- ``frozen``: bought and kept frozen.

A standard-list entry carries its own value. An ingredient typed in by name
takes the default for its category here, until a person sets one. Pure: no I/O.
"""

from __future__ import annotations

from typing import Literal, get_args

from app.catalog import categories

Perishability = Literal["shelf_stable", "refrigerated", "fresh", "frozen"]

VALUES: tuple[Perishability, ...] = get_args(Perishability)

# What an ingredient of each category usually is, before anyone says otherwise.
BY_CATEGORY: dict[categories.CategoryKey, Perishability] = {
    "produce": "fresh",
    "dairy": "refrigerated",
    "meat": "fresh",
    "seafood": "fresh",
    "bakery": "fresh",
    "frozen": "frozen",
    "pantry": "shelf_stable",
    "spices": "shelf_stable",
    "beverages": "shelf_stable",
}

FALLBACK: Perishability = "shelf_stable"


def default_for(category: str | None) -> Perishability:
    """The perishability an ingredient in ``category`` (free text) starts with."""
    key = categories.key(category)
    return BY_CATEGORY[key] if key is not None else FALLBACK
