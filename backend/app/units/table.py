"""The unit table: physical constants, US customary volumes, and aliases."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

Dimension = Literal["mass", "volume", "count"]
System = Literal["us", "metric", "any"]

BASE_UNIT: dict[str, str] = {"mass": "g", "volume": "ml", "count": "each"}

# `T` and `t` are matched case-sensitively by the parser; every other alias is
# matched case-insensitively. Aliases are stored lowercased except those two.
CASE_SENSITIVE_ALIASES: dict[str, str] = {"T": "tbsp", "t": "tsp"}


@dataclass(frozen=True, slots=True)
class Unit:
    code: str
    dimension: Dimension
    to_base_factor: Decimal  # multiply a quantity in this unit to reach g, ml, or each
    system: System
    aliases: tuple[str, ...]


def _u(code: str, dimension: Dimension, factor: str, system: System, *aliases: str) -> Unit:
    return Unit(code, dimension, Decimal(factor), system, tuple(aliases))


SEED_UNITS: tuple[Unit, ...] = (
    _u("mg", "mass", "0.001", "metric", "milligram", "milligrams", "mgs"),
    _u("g", "mass", "1", "metric", "gram", "grams", "gm", "gms", "gr"),
    _u("kg", "mass", "1000", "metric", "kilogram", "kilograms", "kgs", "kilo", "kilos"),
    _u("oz", "mass", "28.349523125", "us", "ounce", "ounces", "ozs"),
    _u("lb", "mass", "453.59237", "us", "pound", "pounds", "lbs"),
    _u(
        "ml",
        "volume",
        "1",
        "metric",
        "milliliter",
        "milliliters",
        "millilitre",
        "millilitres",
        "mls",
        "cc",
    ),
    _u("l", "volume", "1000", "metric", "liter", "liters", "litre", "litres", "ltr", "ltrs"),
    _u("tsp", "volume", "4.92892159375", "us", "teaspoon", "teaspoons", "tsps"),
    _u(
        "tbsp",
        "volume",
        "14.78676478125",
        "us",
        "tablespoon",
        "tablespoons",
        "tbsps",
        "tbs",
        "tbl",
        "tblsp",
    ),
    _u(
        "fl_oz",
        "volume",
        "29.5735295625",
        "us",
        "fl oz",
        "floz",
        "fluid ounce",
        "fluid ounces",
        "fl ounce",
        "fl ounces",
    ),
    _u("cup", "volume", "236.5882365", "us", "cups", "c"),
    _u("pt", "volume", "473.176473", "us", "pint", "pints", "pts"),
    _u("qt", "volume", "946.352946", "us", "quart", "quarts", "qts"),
    _u("gal", "volume", "3785.411784", "us", "gallon", "gallons", "gals"),
    _u("each", "count", "1", "any", "ea", "piece", "pieces", "pc", "pcs", "unit", "units", "ct"),
    _u("dozen", "count", "12", "any", "doz", "dz", "dozens"),
)


def units_by_code(units: Iterable[Unit] = SEED_UNITS) -> Mapping[str, Unit]:
    return {u.code: u for u in units}
