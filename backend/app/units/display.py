"""Per-unit prices for display: per lb, oz or fl oz (or kg and L), never per gram (issue 245).

Prices are stored and compared per canonical unit (g, ml, each: non-negotiable 5).
This module only turns such a price into the unit a person reads, with Decimal
arithmetic throughout. It is pure: the caller says which display system to use and,
for weights, how heavy the ingredient's packs usually are.

Rules (04, 2E "Showing a unit price"):
- weight, US: per lb, or per oz when the ingredient's usual pack is under 1 lb, so
  one ingredient always reads in one unit and a comparison row is like for like;
- weight, metric: per kg;
- volume: per fl oz (US) or per L (metric);
- count: each;
- 2 decimal places, or 3 when the price is under $1, so close small prices differ.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from app.units.table import SEED_UNITS, units_by_code

DisplaySystem = Literal["us", "metric"]

_UNITS = units_by_code(SEED_UNITS)
POUND_G = _UNITS["lb"].to_base_factor

# What each display unit is called on screen.
LABELS: dict[str, str] = {
    "lb": "lb",
    "oz": "oz",
    "fl_oz": "fl oz",
    "kg": "kg",
    "l": "L",
    "each": "each",
}

_TWO = Decimal("0.01")
_THREE = Decimal("0.001")


@dataclass(frozen=True, slots=True)
class DisplayPrice:
    """A unit price as shown: ``price`` per ``unit`` (``unit`` is a label, e.g. "fl oz")."""

    price: Decimal
    unit: str


def mass_unit(system: DisplaySystem, usual_pack_g: Decimal | None) -> str:
    """The weight unit an ingredient's prices read in.

    US: per oz when the ingredient's usual pack (the median weight bought) is under
    a pound, otherwise per lb, also when nothing is known yet. Metric: per kg.
    """
    if system == "metric":
        return "kg"
    if usual_pack_g is not None and usual_pack_g < POUND_G:
        return "oz"
    return "lb"


def display_unit(norm_unit: str, system: DisplaySystem, mass: str) -> str | None:
    """The unit code a price per ``norm_unit`` is shown in, or None for an unknown unit."""
    if norm_unit == "g":
        return mass
    if norm_unit == "ml":
        return "fl_oz" if system == "us" else "l"
    if norm_unit == "each":
        return "each"
    return None


def round_price(value: Decimal) -> Decimal:
    """2 decimal places, or 3 below $1."""
    return value.quantize(_THREE if abs(value) < 1 else _TWO, rounding=ROUND_HALF_UP)


def per_unit(norm_price: Decimal, norm_unit: str, target: str) -> Decimal:
    """A price per canonical unit restated per ``target`` (unrounded).

    A price per gram times the grams in a pound is the price per pound.
    """
    if norm_unit == target:
        return norm_price
    if target == "each" or norm_unit == "each":
        raise ValueError(f"cannot restate a price per {norm_unit} per {target}")
    unit = _UNITS[target]
    if unit.dimension != _UNITS[norm_unit].dimension:
        raise ValueError(f"{target} is not a {_UNITS[norm_unit].dimension} unit")
    return norm_price * unit.to_base_factor / _UNITS[norm_unit].to_base_factor


def show(
    norm_price: Decimal | None,
    norm_unit: str | None,
    system: DisplaySystem,
    mass: str | None = None,
) -> DisplayPrice | None:
    """A stored unit price as a person reads it, or None when it isn't normalized."""
    if norm_price is None or not norm_unit:
        return None
    target = display_unit(norm_unit, system, mass or mass_unit(system, None))
    if target is None:
        return None
    return DisplayPrice(round_price(per_unit(norm_price, norm_unit, target)), LABELS[target])
