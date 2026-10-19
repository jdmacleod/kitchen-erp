"""A weight or count as a till prints it, read from the row's own text.

Receipt text is untrusted: it is matched against these patterns, never
interpreted. :func:`quantity_only` reads a row that holds nothing but a quantity
at a rate; app.ingest.lines reads one printed on an item's own row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from app.units import UnitParseFailure, parse_unit

CENTS = Decimal("0.01")

# A rate as tills print it, with or without a leading zero ("@ $ .65").
_RATE = r"\$?\s*(\d+(?:\.\d+)?|\.\d+)"
# Pounds as OCR reads them too: "Ib", "1b" and "|b" for "lb". The "@ rate" that must
# follow makes the reading safe; a bare "Ib" is only ever a loose weight (below).
_POUND_OCR = re.compile(r"[il1|]bs?", re.IGNORECASE)
# A till that weighs with a tare prints the net weight, then the gross weight in
# brackets: "1.24 (1.25) lb @ 2.99 /lb". The net weight is the one charged.
_GROSS = r"(?:\(\s*\d+(?:\.\d+)?\s*\)\s*)?"
WEIGHT_PATTERN = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?)\s*" + _GROSS + r"(lbs?|[il1|]bs?|kg|oz|g)\b\s*@\s*" + _RATE,
    re.IGNORECASE,
)
COUNT_PATTERN = re.compile(r"(?<![\d.])(\d{1,3})\s*@\s*" + _RATE, re.IGNORECASE)
# A weight whose unit OCR garbled past "Ib": "ll", "|i", "lt", "1k" for "lb", on a
# phone scan of a thermal receipt. Only a decimal weight, a two-character token that
# starts like an "l", and an "@ rate" qualify, and such a row joins an item only when
# the weight at the rate comes to that item's amount (merge_quantity_lines).
GARBLED_WEIGHT_PATTERN = re.compile(
    r"(?<![\d.])(\d+\.\d+)\s*" + _GROSS + r"([il1|!][a-z1|!])(?![A-Za-z0-9])\s*@\s*" + _RATE,
    re.IGNORECASE,
)
# Some tills name the currency before a rate's unit: "0.99 USD/lb".
_CURRENCY = r"(?:\b(?:USD|US\$|CAD|EUR)\s*)?"
_GARBLED_RATE_UNIT = re.compile(_CURRENCY + r"/\s*[il1|!][a-z1|!]?(?![A-Za-z0-9])", re.I)
# A weight that is printed but did not survive OCR cleanly: "1.24 1b", "0.85 Ib", a
# weight with no "@ price". Enough to know the line is weighed, not enough to read it.
# Grams count only with more after them on the line: a trailing single letter is as
# likely a tax code ("2.49 G") as a unit.
LOOSE_WEIGHT_PATTERN = re.compile(
    r"(?<![\d.])\d+\.\d+\s*" + _GROSS + r"(?:(?:lbs?|[1i]bs?|kg|oz)\b|g\b(?=\s*\S))",
    re.IGNORECASE,
)
_AMOUNT = re.compile(r"(\d+)[.,](\d{2})")


def _amounts(text: str) -> list[Decimal]:
    return [Decimal(f"{whole}.{cents}") for whole, cents in _AMOUNT.findall(text)]


def _unit(text: str | None) -> str | None:
    if text is None or not text.strip():
        return None
    if _POUND_OCR.fullmatch(text.strip()):
        return "lb"
    parsed = parse_unit(text)
    return None if isinstance(parsed, UnitParseFailure) else parsed


@dataclass(frozen=True)
class PrintedQuantity:
    """A row that is only a weight or count, as printed."""

    qty: Decimal
    unit: str
    rate: Decimal
    amount: Decimal | None  # what it comes to, when the row prints that too
    unit_misread: bool = False  # the unit was OCR's garble of "lb"

    def comes_to(self, total: Decimal) -> bool:
        """The quantity at the rate is ``total``, to within a cent of rounding."""
        return abs(self.qty * self.rate - total) <= CENTS


# "/lb" after a rate, and what may be left once the quantity and amounts are out
# of a quantity row: nothing, or a tax letter or two.
_RATE_UNIT = re.compile(_CURRENCY + r"/\s*(?:lbs?|[il1|]bs?|kg|oz|g|ea|each)\b", re.I)
_TAX_CODE = re.compile(r"[A-Za-z*]{0,2}")


def quantity_only(raw_text: str) -> PrintedQuantity | None:
    """The row's weight or count when that is all the row holds, else None.

    "2.71 lb @ 3.99 /lb" and "5 @ 0.79" qualify, as does "2.31 lb @ 0.69/lb 1.59 F"
    with its amount and tax letter. A row with a name ("AVOCADO 3 @ 0.69 2.07"), a
    negative amount or two amounts does not. A weight whose "lb" OCR garbled
    ("1.30 ll @ 2.00 /lt") is read as pounds and marked ``unit_misread``.
    """
    weighed = WEIGHT_PATTERN.search(raw_text)
    garbled = None if weighed else GARBLED_WEIGHT_PATTERN.search(raw_text)
    match = weighed or garbled or COUNT_PATTERN.search(raw_text)
    if match is None:
        return None
    rest = _RATE_UNIT.sub(" ", raw_text[: match.start()] + " " + raw_text[match.end() :])
    if garbled is not None:
        rest = _GARBLED_RATE_UNIT.sub(" ", rest)
    amounts = _amounts(rest)
    rest = _AMOUNT.sub(" ", rest).replace("$", " ")
    if len(amounts) > 1 or not _TAX_CODE.fullmatch("".join(rest.split())):
        return None
    if weighed is not None:
        qty, unit, rate = Decimal(match.group(1)), _unit(match.group(2)), match.group(3)
    elif garbled is not None:
        amount = amounts[0] if amounts else None
        return PrintedQuantity(Decimal(match.group(1)), "lb", Decimal(match.group(3)), amount, True)
    else:
        qty, unit, rate = Decimal(match.group(1)), "each", match.group(2)
    if unit is None:
        return None
    return PrintedQuantity(qty, unit, Decimal(rate), amounts[0] if amounts else None)
