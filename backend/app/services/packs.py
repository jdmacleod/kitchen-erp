"""Whole packs of a product (07, 3D; 17, 4B): shared by recipe costing and the planner.

A product's pack is ``pack_qty`` of ``pack_unit``, optionally with ``pack_count``
pieces. ``pack_canonical_qty`` says how much of an ingredient's canonical unit
one pack holds, through ``convert`` with the product's own context and the pack
itself stripped (a pack is never expressed in terms of itself); when the
canonical unit is a count and the pack states its pieces, one pack is that many.
``basket`` rounds a quantity up to whole packs at the pack price, or costs it at
quantity times unit price when the product has no usable pack. Money is
quantized to four places, ROUND_HALF_EVEN, the price book's rule.

Pure: no database, no I/O.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Context, Decimal
from typing import Protocol

from app.units import ConversionContext, ConversionFailure, ProductContext, convert
from app.units.convert import BUNCH

CTX = Context(prec=28, rounding=ROUND_HALF_EVEN)
FOUR = Decimal("0.0001")


class Packaged(Protocol):
    """What a pack needs of a product row (``app.models.catalog.Product`` has them)."""

    pack_qty: Decimal | None
    pack_unit: str | None
    pack_count: int | None


def money(value: Decimal) -> Decimal:
    return value.quantize(FOUR, rounding=ROUND_HALF_EVEN)


def pack_canonical_qty(product: Packaged, context: ConversionContext) -> Decimal | None:
    """How much of the canonical unit one pack of the product holds, or None."""
    if product.pack_qty is None or product.pack_unit is None:
        return None
    if context.canonical_unit == "each" and product.pack_count:
        return Decimal(product.pack_count)
    inner = context.product
    # A pack is expressed in units, never in measures, except a pack of bunches:
    # only the ingredient's "bunch" measure weighs one (issue 371).
    bunch = tuple(m for m in context.measures if m.label.strip().lower() == BUNCH)
    without_pack = replace(
        context,
        measures=bunch if product.pack_unit == BUNCH else (),
        product=replace(inner, pack=None) if inner is not None else ProductContext(),
    )
    result = convert(product.pack_qty, product.pack_unit, without_pack)
    if isinstance(result, ConversionFailure) or result.qty <= 0:
        return None
    return result.qty


def pack_price(unit_price: Decimal, pack_qty: Decimal) -> Decimal:
    """What one pack costs at a normalized unit price."""
    return money(CTX.multiply(unit_price, pack_qty))


def packs_for(qty: Decimal, pack_qty: Decimal) -> Decimal:
    """Whole packs that hold ``qty``, rounded up."""
    return CTX.divide(qty, pack_qty).to_integral_value(rounding=ROUND_CEILING)


def basket(
    qty: Decimal, unit_price: Decimal, pack_qty: Decimal | None
) -> tuple[Decimal, Decimal | None]:
    """Basket cost and packs for one quantity: whole packs times the pack price,
    or quantity times unit price (packs None) without a pack size."""
    if pack_qty is None:
        return money(CTX.multiply(qty, unit_price)), None
    packs = packs_for(qty, pack_qty)
    return money(CTX.multiply(packs, pack_price(unit_price, pack_qty))), packs
