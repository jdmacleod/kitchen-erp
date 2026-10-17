"""The price book's one rounding rule, in a module that imports nothing of the app.

Arithmetic in a 28-digit Decimal context; the normalized unit price is quantized
to six places, ROUND_HALF_EVEN (spec 04, 2A). ``pricebook`` normalizes with it,
and read models that work out a unit price when read (``regular_prices``) use it
too without importing ``pricebook``, which would make an import cycle.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Context, Decimal

_CTX = Context(prec=28, rounding=ROUND_HALF_EVEN)
_SIX = Decimal("0.000001")


def unit_price(price: Decimal, canonical_qty: Decimal) -> Decimal:
    return _CTX.divide(price, canonical_qty).quantize(_SIX, rounding=ROUND_HALF_EVEN)
