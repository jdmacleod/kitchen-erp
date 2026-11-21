"""Front matter `yield:` as what a recipe makes: an amount and a unit (#526, ruling S1).

`yield: 500 ml`, `yield: 1.5 l` and `yield: 2 cups` parse; `yield: 12 pieces`,
`yield: lots`, a count, an amount of zero or less, and a range (`400-500 ml`) do not, so
a recipe reference never scales by a guess or divides by zero. Pure, like the
parser. Code calls this "makes" so it never reads as an ingredient's trim yield.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from app.recipes.cooklang.quantities import parse_number
from app.units.parse import parse_unit
from app.units.table import SEED_UNITS

# A count ("12 pieces") is not a yield a reference can scale yet (TODOS: count
# yields as pieces), so only mass and volume parse.
_SCALABLE = {unit.code for unit in SEED_UNITS if unit.dimension in ("mass", "volume")}

# The amount is everything up to the last run of letters (and spaces, periods)
# at the end: "1 1/2 cups" splits as "1 1/2" and "cups", "500ml" as "500" and "ml".
_SPLIT = re.compile(r"^(?P<amount>.*?[0-9½¼¾⅓⅔⅛⅜⅝⅞])\s*(?P<unit>[^\W\d_][\w .]*)$")


@dataclass(frozen=True, slots=True)
class YieldParseFailure:
    text: str
    # "empty", "no_unit", "unknown_unit", "not_mass_or_volume", "not_a_number", "not_positive"
    reason: str


def parse_yield(text: object) -> tuple[Decimal, str] | YieldParseFailure:
    """`"500 ml"` → `(Decimal("500"), "ml")`, or a failure saying why. Never raises."""
    if not isinstance(text, str) or not text.strip():
        return YieldParseFailure(str(text or ""), "empty")
    stripped = text.strip()
    match = _SPLIT.match(stripped)
    if match is None:
        return YieldParseFailure(stripped, "no_unit")
    amount = parse_number(match.group("amount"))
    if amount is None:
        return YieldParseFailure(stripped, "not_a_number")  # a range, a word, "1-2"
    if amount <= 0:
        return YieldParseFailure(stripped, "not_positive")
    unit = parse_unit(match.group("unit"))
    if not isinstance(unit, str):
        return YieldParseFailure(stripped, "unknown_unit")
    if unit not in _SCALABLE:
        return YieldParseFailure(stripped, "not_mass_or_volume")
    return amount, unit
