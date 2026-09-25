"""Ingredient category keys (docs/spec/08-ui-design-system.md, D12).

``ingredient.category`` is nullable free text. This module is the only place
that decides which of the nine display keys a category belongs to: the API
returns the result as ``category_key``, the product list filters on it, and
the frontend's CategoryChip renders it without a synonym map of its own.

Adding a category is one entry in ``SYNONYMS`` here plus two lines in
``frontend/src/theme.css``. Anything unrecognized has no key and gets the
neutral chip, so a new free-text value never breaks anything.
"""

from __future__ import annotations

from typing import Literal, get_args

CategoryKey = Literal[
    "produce",
    "dairy",
    "meat",
    "seafood",
    "bakery",
    "pantry",
    "spices",
    "beverages",
    "frozen",
]

KEYS: tuple[CategoryKey, ...] = get_args(CategoryKey)

# Normalized free text (see ``normalize``) to key. Every key maps to itself.
SYNONYMS: dict[str, CategoryKey] = {
    "produce": "produce",
    "vegetable": "produce",
    "vegetables": "produce",
    "veg": "produce",
    "fruit": "produce",
    "fruits": "produce",
    "herbs": "produce",
    "greens": "produce",
    "dairy": "dairy",
    "cheese": "dairy",
    "eggs": "dairy",
    "milk": "dairy",
    "meat": "meat",
    "poultry": "meat",
    "beef": "meat",
    "pork": "meat",
    "charcuterie": "meat",
    "seafood": "seafood",
    "fish": "seafood",
    "shellfish": "seafood",
    "bakery": "bakery",
    "bread": "bakery",
    "pantry": "pantry",
    "dry goods": "pantry",
    "grains": "pantry",
    "canned": "pantry",
    "baking": "pantry",
    "oils": "pantry",
    "condiments": "pantry",
    "spices": "spices",
    "spice": "spices",
    "seasoning": "spices",
    "seasonings": "spices",
    "beverages": "beverages",
    "beverage": "beverages",
    "drinks": "beverages",
    "coffee": "beverages",
    "tea": "beverages",
    "frozen": "frozen",
}


def normalize(text: str) -> str:
    """Case-fold and collapse whitespace, so "  Dry   Goods " reads as "dry goods"."""
    return " ".join(text.casefold().split())


def key(category: str | None) -> CategoryKey | None:
    """The display key for a free-text category, or None when it has none."""
    if category is None:
        return None
    return SYNONYMS.get(normalize(category))
