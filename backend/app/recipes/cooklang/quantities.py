"""Quantity text to Decimal, exactly. `1/2`, `1 1/2`, `¾`, `2½`, `2-3`, `2 to 3`; `some` stays text.

Nothing here touches a float. A fraction whose decimal expansion does not
terminate (`1/3`) is divided under an explicit 28-digit context so the answer is
the same wherever it is computed.
"""

from __future__ import annotations

import re
from decimal import Decimal, localcontext

from app.recipes.cooklang.model import (
    NO_QUANTITY,
    Quantity,
    QuantityNumber,
    QuantityRange,
    QuantityText,
)

VULGAR_FRACTIONS: dict[str, tuple[int, int]] = {
    "¼": (1, 4),
    "½": (1, 2),
    "¾": (3, 4),
    "⅓": (1, 3),
    "⅔": (2, 3),
    "⅕": (1, 5),
    "⅖": (2, 5),
    "⅗": (3, 5),
    "⅘": (4, 5),
    "⅙": (1, 6),
    "⅚": (5, 6),
    "⅐": (1, 7),
    "⅛": (1, 8),
    "⅜": (3, 8),
    "⅝": (5, 8),
    "⅞": (7, 8),
    "⅑": (1, 9),
    "⅒": (1, 10),
}

# A whole number never carries a leading zero (`01/2` is text, as the canonical
# suite requires); a decimal fraction needs digits on both sides of the point.
_INT = r"(?:0|[1-9][0-9]*)"
_DECIMAL = rf"{_INT}(?:\.[0-9]+)?"
_VULGAR = "[" + "".join(VULGAR_FRACTIONS) + "]"
_NUMBER = (
    rf"(?P<whole>{_INT})\s*/\s*(?P<den>{_INT})"  # 1/2, 1 / 2
    rf"|(?P<mwhole>{_INT})\s+(?P<mnum>{_INT})\s*/\s*(?P<mden>{_INT})"  # 1 1/2
    rf"|(?P<vwhole>{_INT})?\s*(?P<vulgar>{_VULGAR})"  # ¾, 2½, 2 ½
    rf"|(?P<plain>{_DECIMAL})"  # 3, 1.5
)
_NUMBER_RE = re.compile(rf"^(?:{_NUMBER})$")
_RANGE_RE = re.compile(r"^(?P<low>.+?)\s*(?:[-–—]|\s[Tt][Oo]\s)\s*(?P<high>.+)$")


def _divide(numerator: int, denominator: int) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 28
        return Decimal(numerator) / Decimal(denominator)


def parse_number(text: str) -> Decimal | None:
    """One number in any accepted form, or None when the text is not one."""
    match = _NUMBER_RE.match(text.strip())
    if match is None:
        return None
    groups = match.groupdict()
    if groups["plain"] is not None:
        return Decimal(groups["plain"])
    if groups["vulgar"] is not None:
        numerator, denominator = VULGAR_FRACTIONS[groups["vulgar"]]
        whole = int(groups["vwhole"]) if groups["vwhole"] else 0
        return Decimal(whole) + _divide(numerator, denominator)
    if groups["mwhole"] is not None:
        denominator = int(groups["mden"])
        if denominator == 0:
            return None
        return Decimal(groups["mwhole"]) + _divide(int(groups["mnum"]), denominator)
    denominator = int(groups["den"])
    if denominator == 0:
        return None
    return _divide(int(groups["whole"]), denominator)


def parse_quantity(text: str) -> Quantity:
    """Number, range, text, or none. Never raises; anything unrecognised is text."""
    stripped = text.strip()
    if not stripped:
        return NO_QUANTITY
    number = parse_number(stripped)
    if number is not None:
        return QuantityNumber(number)
    # A range is two numbers around a dash or the word "to". The lazy low side
    # lets `1 1/2 - 2` and `2 to 3` both split in the right place; a text such
    # as `1-2-3` or `7 k` falls through to text.
    for match in _RANGE_RE.finditer(stripped):
        low = parse_number(match.group("low"))
        high = parse_number(match.group("high"))
        if low is not None and high is not None:
            return QuantityRange(low, high)
    return QuantityText(stripped)
