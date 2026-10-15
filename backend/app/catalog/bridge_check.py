"""Whether a density or a named measure is a value no food could have (issue 356).

The server's copy of ``frontend/src/lib/measureCheck.ts``, with the same bounds
and wording, so the unconfirmed list can put implausible values first. The
check is advisory: nothing is refused because of it.

- A density outside 0.05 to 2.5 g/ml is lighter or heavier than any food.
- A measure whose label starts with a volume unit ("tbsp juice") implies a
  density, which must be inside that band.
- A measure whose label starts with a mass unit ("oz, boneless") must weigh
  within 25% of that unit.

Only ingredients counted by mass are checked; a label that starts with no unit
("clove", "medium") can't be.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from app.units import Unit

# Foods run from about 0.05 g/ml (dried leaves, popcorn) to about 2 g/ml (salt,
# honey); anything outside this band is a typo or an inverted entry.
MIN_G_PER_ML = Decimal("0.05")
MAX_G_PER_ML = Decimal("2.5")
MASS_TOLERANCE = Decimal("0.25")
_SPLIT = re.compile(r"[\s,()]+")


@dataclass(frozen=True)
class MeasureReading:
    """What a measure label says about its value: the implied g/ml, and why it is off."""

    g_per_ml: Decimal | None
    problem: str | None


def sig(value: Decimal, figures: int) -> Decimal:
    """``figures`` significant figures, as the frontend's ``toPrecision`` gives them."""
    step = Decimal(1).scaleb(value.adjusted() - figures + 1)
    return Decimal(format(value.quantize(step).normalize(), "f"))


def _two(value: Decimal) -> str:
    return format(sig(value, 2), "f")


def leading_unit(label: str, units: Mapping[str, Unit]) -> Unit | None:
    """The unit a measure label starts with: "tbsp juice" gives tbsp, "fl oz" gives fl oz."""
    words = [w for w in _SPLIT.split(label.lower()) if w]
    for n in (2, 1):
        if len(words) < n:
            continue
        head = " ".join(words[:n])
        for unit in units.values():
            if head == unit.code or head in unit.aliases:
                return unit
    return None


def density_problem(g_per_ml: Decimal) -> str | None:
    if MIN_G_PER_ML <= g_per_ml <= MAX_G_PER_ML:
        return None
    side = "lighter" if g_per_ml < MIN_G_PER_ML else "heavier"
    return f"{_two(g_per_ml)} g/ml is {side} than any food."


def read_measure(
    label: str, qty: Decimal, canonical_unit: str, units: Mapping[str, Unit]
) -> MeasureReading:
    canonical = units.get(canonical_unit)
    if canonical is None or canonical.dimension != "mass" or qty <= 0:
        return MeasureReading(None, None)
    unit = leading_unit(label, units)
    if unit is None or unit.code == canonical.code:
        return MeasureReading(None, None)
    grams = qty * canonical.to_base_factor
    if unit.dimension == "volume":
        density = grams / unit.to_base_factor
        if MIN_G_PER_ML <= density <= MAX_G_PER_ML:
            return MeasureReading(sig(density, 3), None)
        side = "lighter" if density < MIN_G_PER_ML else "heavier"
        return MeasureReading(
            sig(density, 3),
            f"One {label} at {_plain(qty)} {canonical_unit} would be {_two(density)} g/ml, "
            f"{side} than any food. Check that {_plain(qty)} {canonical_unit} is what one "
            f"{label} weighs.",
        )
    if unit.dimension == "mass":
        expected = unit.to_base_factor
        if abs(grams - expected) <= expected * MASS_TOLERANCE:
            return MeasureReading(None, None)
        return MeasureReading(
            None,
            f"One {label} at {_plain(qty)} {canonical_unit} doesn't match 1 {unit.code} "
            f"({_plain(sig(expected, 3))} g).",
        )
    return MeasureReading(None, None)


def _plain(value: Decimal) -> str:
    return format(value.normalize(), "f")
