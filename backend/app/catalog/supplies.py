"""Kitchen supply categories (04, 2T; issue 262).

A receipt line can be marked a kitchen supply instead of ignored: it keeps its
amount and one of these categories, emits no price, and is counted as kitchen
supply spend. The boundary is cooking and storing food. Cleaning, serving and
appliance supplies are not kitchen supplies here: they stay ignored.

This module is the only place the categories and their names are listed; the
API serves them (``GET /supply-categories``) and the frontend renders what it
is sent. The keys are stored, so a key is never renamed; a label may be.
"""

from __future__ import annotations

from typing import Literal, get_args

SupplyCategory = Literal["wraps", "storage", "cooking", "filters", "fuel"]

KEYS: tuple[SupplyCategory, ...] = get_args(SupplyCategory)

# Key to (label, what it covers). The examples say where an item belongs.
CATEGORIES: dict[SupplyCategory, tuple[str, str]] = {
    "wraps": ("Wraps and papers", "foil, parchment, wax paper, plastic wrap, beeswax wraps"),
    "storage": (
        "Bags and containers",
        "zip-top and freezer bags, vacuum-sealer bags, deli containers, foil pans, food labels",
    ),
    "cooking": (
        "Cooking aids",
        "baking cups, oven and slow-cooker liners, twine, cheesecloth, skewers, piping bags, "
        "food-safe gloves, canning lids",
    ),
    "filters": ("Coffee and tea filters", "paper coffee filters, tea filters"),
    "fuel": ("Grilling fuel", "charcoal, wood chips, pellets"),
}
