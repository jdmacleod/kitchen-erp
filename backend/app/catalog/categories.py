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

import re
from itertools import pairwise
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
    # USDA FoodData Central food groups (Foundation and SR Legacy), so a food's
    # group maps onto a key (1G). Groups with no household meaning stay unmapped.
    "dairy and egg products": "dairy",
    "spices and herbs": "spices",
    "fats and oils": "pantry",
    "poultry products": "meat",
    "soups, sauces, and gravies": "pantry",
    "sausages and luncheon meats": "meat",
    "breakfast cereals": "pantry",
    "fruits and fruit juices": "produce",
    "pork products": "meat",
    "vegetables and vegetable products": "produce",
    "nut and seed products": "pantry",
    "beef products": "meat",
    "finfish and shellfish products": "seafood",
    "legumes and legume products": "pantry",
    "lamb, veal, and game products": "meat",
    "baked products": "bakery",
    "sweets": "pantry",
    "cereal grains and pasta": "pantry",
}


def normalize(text: str) -> str:
    """Case-fold and collapse whitespace, so "  Dry   Goods " reads as "dry goods"."""
    return " ".join(text.casefold().split())


def key(category: str | None) -> CategoryKey | None:
    """The display key for a free-text category, or None when it has none."""
    if category is None:
        return None
    return SYNONYMS.get(normalize(category))


def stored(category: str | None) -> str | None:
    """What an ingredient keeps (#344): the key when the text has one, else the
    text with its spaces tidied; blank is no category."""
    if category is None or not category.strip():
        return None
    text = " ".join(category.split())
    return key(text) or text


def key_from_text(text: str | None) -> CategoryKey | None:
    """A key from a page's or a model's wording ("Frozen Vegetables", "Pantry >
    Canned goods"): the whole text, else "frozen" when it says so, else the first
    word or pair of words that names a key. None when nothing does."""
    if not text or not text.strip():
        return None
    if (whole := key(text)) is not None:
        return whole
    words = normalize(re.sub(r"[>/&,]", " ", text)).split()
    if "frozen" in words:
        return "frozen"
    pairs = [f"{a} {b}" for a, b in pairwise(words)]
    for phrase in [*pairs, *words]:
        if (found := SYNONYMS.get(phrase)) is not None:
            return found
    return None


# Words in an ingredient's name that settle its category whatever else it says
# (``key_from_name``). Data, not logic: add freely, but never a word that also
# names a food of another category ("bass" stays: sea bass and striped bass are
# both fish, and no other food is called bass).
_SEAFOOD_WORDS = frozenset(
    {
        "anchovy", "anchovies", "bass", "branzino", "catfish", "clam", "clams", "cod",
        "crab", "crabs", "crawfish", "fish", "flounder", "grouper", "haddock", "halibut",
        "herring", "lobster", "lobsters", "mackerel", "mahi", "mussel", "mussels",
        "octopus", "oyster", "oysters", "pollock", "prawn", "prawns", "salmon",
        "sardine", "sardines", "scallop", "scallops", "seabass", "shrimp", "snapper",
        "sole", "squid", "swordfish", "tilapia", "trout", "tuna", "whitefish", "yellowtail",
    }
)  # fmt: skip
# How a food was kept or made, which puts it on a shelf rather than in the
# produce, meat or seafood case: "canned chipotle peppers", "fried shallots".
_SHELF_WORDS = frozenset({"canned", "jarred", "dried", "dehydrated", "crispy", "fried", "pickled"})
_SPICE_WORDS = frozenset({"seasoning", "seasonings", "masala", "spice", "spices", "rub"})


def key_from_name(name: str | None) -> CategoryKey | None:
    """The category an ingredient's own words settle, or None when they don't.

    "frozen" anywhere wins: a frozen food is filed with the frozen foods whatever
    it is. Then a spice blend, a fish or shellfish, and a food kept on a shelf
    (canned, dried, fried). A fish that is canned or dried is pantry, so the shelf
    words come before the fish."""
    if not name:
        return None
    words = normalize(re.sub(r"[^\w\s'-]", " ", name)).split()
    if "frozen" in words:
        return "frozen"
    if _SPICE_WORDS.intersection(words):
        return "spices"
    if _SHELF_WORDS.intersection(words):
        return "pantry"
    if _SEAFOOD_WORDS.intersection(words):
        return "seafood"
    return None


# The filed keys a name word overrides (``misfiled``); "frozen" overrides any.
# The others override only what their word rules out beyond doubt: a fish is no
# meat, and a canned, dried or seasoning food is not kept in a fresh case. A fish
# filed under pantry or frozen may be canned or frozen; "dried oregano" is a spice,
# "spice cake" bakes, and "fish sauce" is pantry.
_FRESH: frozenset[CategoryKey] = frozenset({"produce", "meat", "seafood", "dairy"})
_OVERRIDES: dict[CategoryKey, frozenset[CategoryKey]] = {
    "seafood": frozenset({"meat"}),
    "pantry": _FRESH,
    "spices": _FRESH,
}


def misfiled(name: str | None, category: str | None) -> CategoryKey | None:
    """The key an ingredient filed under ``category`` belongs under instead, when its
    name settles it (issue 372); None when the filing stands or nothing says otherwise.

    A frozen food filed anywhere else is offered frozen; a fish filed under meat,
    seafood; a canned, dried or fried food or a seasoning filed in a fresh case
    (produce, meat, seafood, dairy), pantry or spices."""
    by_name = key_from_name(name)
    filed = key(category)
    if category is None or by_name is None or by_name == filed:
        return None
    if by_name == "frozen":
        return by_name
    return by_name if filed in _OVERRIDES.get(by_name, frozenset()) else None
