"""One recipe line's cost (07, 3D): the prices that qualify, the one a basis
chooses, and the line's canonical quantity, yield and consumed and basket costs.

``services/recipe_costing`` describes the rules and assembles these lines into a
recipe's costing and its snapshots.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Ingredient, Product, RecipeIngredient
from app.services.packs import CTX as _CTX
from app.services.packs import basket as _basket
from app.services.packs import money as _money
from app.services.packs import pack_canonical_qty as _pack_canonical_qty
from app.services.units import build_context
from app.units import ConversionContext, ConversionFailure, convert

Basis = Literal["latest", "average", "cheapest", "offers"]
# The bases a snapshot is kept under. "offers" (this week's, 18 X2) is a per-kitchen
# view only, worked out on demand and never kept, so no stored cost reads an offer.
BASES: tuple[Basis, ...] = ("latest", "average", "cheapest")

_FOUR = Decimal("0.0001")
_SIX = Decimal("0.000001")


# --- qualifying prices ----------------------------------------------------------------

# One row per observation (offer_applicable repeats a chain price per location),
# under the price book's default rules: price_current (non-voided, no posted
# prices) applying at an active location, normalized, settled, active product.
# ``home_base_id`` keeps only the rows applying at that kitchen's locations (17,
# 4A, ruling L3): a chain price still counts at the kitchen's branch of the chain.
_QUALIFYING_SQL = """
    SELECT DISTINCT ON (oa.observation_id)
           oa.observation_id, oa.product_id, p.ingredient_id, oa.observed_at,
           oa.norm_unit_price, oa.norm_unit
    FROM offer_applicable oa
    JOIN product p ON p.id = oa.product_id AND p.active
    JOIN vendor_location vl ON vl.id = oa.applies_to_location_id
    LEFT JOIN purchase_line pl ON pl.id = oa.purchase_line_id
    LEFT JOIN purchase pu ON pu.id = pl.purchase_id
    WHERE p.ingredient_id = ANY(CAST(:ids AS uuid[]))
      AND oa.norm_unit_price IS NOT NULL
      AND (oa.purchase_line_id IS NULL OR pu.status = 'committed')
      AND (CAST(:min_quality AS integer) IS NULL
           OR coalesce(p.quality_rating, 0) >= CAST(:min_quality AS integer))
      AND (CAST(:home_base_id AS uuid) IS NULL
           OR vl.home_base_id = CAST(:home_base_id AS uuid))
    ORDER BY oa.observation_id
"""


@dataclass(frozen=True, slots=True)
class QualifyingPrice:
    observation_id: uuid.UUID
    product_id: uuid.UUID
    ingredient_id: uuid.UUID
    observed_at: datetime
    norm_unit_price: Decimal
    norm_unit: str


@dataclass(frozen=True, slots=True)
class OfferPrice:
    """A kitchen store's offer valid today for a product of the line's ingredient."""

    product_id: uuid.UUID
    location_id: uuid.UUID
    offer_id: uuid.UUID
    unit_price: Decimal
    unit: str
    estimate: bool
    # One of the offer in the canonical unit: the basket buys these, not the catalog
    # pack; None for a price by weight.
    pack_qty: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Chosen:
    """The unit price a basis settled on, and the observation that stands for it."""

    norm_unit_price: Decimal
    observation: QualifyingPrice


async def qualifying_prices(
    db: AsyncSession,
    ingredient_ids: set[uuid.UUID],
    min_quality: int | None,
    home_base_id: uuid.UUID | None = None,
) -> dict[uuid.UUID, list[QualifyingPrice]]:
    out: dict[uuid.UUID, list[QualifyingPrice]] = {}
    if not ingredient_ids:
        return out
    rows = await db.execute(
        text(_QUALIFYING_SQL),
        {
            "ids": [str(i) for i in ingredient_ids],
            "min_quality": min_quality,
            "home_base_id": home_base_id,
        },
    )
    for r in rows.mappings():
        price = QualifyingPrice(
            observation_id=r["observation_id"],
            product_id=r["product_id"],
            ingredient_id=r["ingredient_id"],
            observed_at=r["observed_at"],
            norm_unit_price=Decimal(str(r["norm_unit_price"])),
            norm_unit=r["norm_unit"],
        )
        out.setdefault(price.ingredient_id, []).append(price)
    return out


def _latest(prices: list[QualifyingPrice]) -> QualifyingPrice:
    return max(prices, key=lambda p: (p.observed_at, str(p.observation_id)))


def choose(
    prices: list[QualifyingPrice], basis: Basis, window_days: int | None, now: datetime
) -> Chosen | None:
    """The unit price a basis picks from the qualifying prices, or None when none qualify.

    ``average`` means the prices observed within ``window_days``; when the
    window holds none, the latest price stands in (and reads stale, since it is
    older than the window), so an old price is used rather than filtered out.
    """
    if not prices:
        return None
    latest = _latest(prices)
    if basis == "latest":
        return Chosen(latest.norm_unit_price, latest)
    if basis == "cheapest":
        best = min(prices, key=lambda p: (p.norm_unit_price, now - p.observed_at))
        return Chosen(best.norm_unit_price, best)
    assert basis == "average" and window_days is not None  # noqa: S101
    since = now - timedelta(days=window_days)
    window = [p for p in prices if p.observed_at >= since]
    if not window:
        return Chosen(latest.norm_unit_price, latest)
    total = sum((p.norm_unit_price for p in window), Decimal(0))
    mean = _CTX.divide(total, Decimal(len(window))).quantize(_SIX, rounding=ROUND_HALF_EVEN)
    return Chosen(mean, _latest(window))


# --- one line ---------------------------------------------------------------------------


@dataclass(slots=True)
class LineCost:
    """What one recipe ingredient came to; ``high`` fields only for a range."""

    recipe_ingredient_id: uuid.UUID
    status: str
    canonical_qty: Decimal | None = None
    canonical_qty_high: Decimal | None = None
    canonical_unit: str | None = None
    yield_applied: Decimal | None = None
    product_id: uuid.UUID | None = None
    observation_id: uuid.UUID | None = None
    norm_unit_price: Decimal | None = None
    consumed_cost: Decimal | None = None
    consumed_cost_high: Decimal | None = None
    basket_cost: Decimal | None = None
    basket_cost_high: Decimal | None = None
    packs: Decimal | None = None
    packs_high: Decimal | None = None
    bridge_kind: str | None = None
    bridge_confirmed: bool | None = None
    failure_code: str | None = None
    # Priced by this week's offer at that store (the "offers" basis), marked an
    # estimate when the offer's size is the product's.
    offer: OfferPrice | None = None

    @property
    def unconfirmed(self) -> bool:
        return self.bridge_confirmed is False


def from_unit_of(line: RecipeIngredient) -> str:
    """The unit or measure label a line's quantity is in; a bare number counts each."""
    if line.unit:
        return line.unit
    if line.unit_text:
        return line.unit_text.strip()
    return "each"


def _line_failure(failure: ConversionFailure, pin_product_id: uuid.UUID | None) -> str:
    """The failure as the line should name it. An unpinned line never counts in packs
    (its pack was set aside above), so "no pack" there means no weight for one piece:
    the fix is the ingredient's measure, not the product's pack size."""
    if failure.code == "no_pack" and pin_product_id is None:
        return "unknown_measure"
    return failure.code


def grosses_up(line: RecipeIngredient, from_unit: str, context: ConversionContext) -> bool:
    """Whether the line's quantity is edible portion to be grossed up by yield (05).

    Mass and volume quantities are edible; counts and named measures are as
    purchased. ``yield_mode`` overrides either way.
    """
    if line.yield_mode == "edible":
        return True
    if line.yield_mode == "as_purchased":
        return False
    unit = context.units.get(from_unit)
    return unit is not None and unit.dimension in ("mass", "volume")


async def cost_line(
    db: AsyncSession,
    line: RecipeIngredient,
    ingredient: Ingredient | None,
    prices: list[QualifyingPrice],
    pin_product_id: uuid.UUID | None,
    basis: Basis,
    window_days: int | None,
    now: datetime,
    offers: list[OfferPrice] | None = None,
) -> LineCost:
    out = LineCost(recipe_ingredient_id=line.id, status="negligible")
    if line.negligible or line.resolution == "ignored" or line.qty is None:
        return out
    if ingredient is None:
        out.status = "unmapped"
        return out
    out.canonical_unit = ingredient.canonical_unit

    candidates = prices
    if pin_product_id is not None:
        candidates = [p for p in prices if p.product_id == pin_product_id]
    candidates = [p for p in candidates if p.norm_unit == ingredient.canonical_unit]
    offer = _best_offer(offers, ingredient.canonical_unit, pin_product_id)
    # An offer prices the line in place of any price-book price (the "offers" basis).
    chosen = (
        None
        if offer is not None
        else choose(candidates, "latest" if basis == "offers" else basis, window_days, now)
    )

    product_id = (
        offer.product_id if offer else chosen.observation.product_id if chosen else pin_product_id
    )
    product = await db.get(Product, product_id) if product_id is not None else None
    out.product_id = product_id
    context = await build_context(db, ingredient, product)
    if context.product is not None and (
        pin_product_id is None or ingredient.canonical_unit == "each"
    ):
        # A recipe's "2 each" is two pieces, never two packs of whatever product
        # priced last; only a pinned product (named by a person) stands in for
        # "1 of it" when the canonical unit is a mass or a volume.
        context = replace(context, product=replace(context.product, pack=None))

    from_unit = from_unit_of(line)
    low = convert(line.qty, from_unit, context)
    if isinstance(low, ConversionFailure):
        out.status = "unconvertible"
        out.failure_code = _line_failure(low, pin_product_id)
        return out
    high = None
    if line.qty_kind == "range" and line.qty_high is not None:
        high = convert(line.qty_high, from_unit, context)
        if isinstance(high, ConversionFailure):
            out.status = "unconvertible"
            out.failure_code = _line_failure(high, pin_product_id)
            return out

    qty_low, qty_high = low.qty, high.qty if high is not None else None
    if grosses_up(line, from_unit, context):
        out.yield_applied = ingredient.yield_pct
        qty_low = _CTX.divide(qty_low, ingredient.yield_pct)
        if qty_high is not None:
            qty_high = _CTX.divide(qty_high, ingredient.yield_pct)
    out.canonical_qty, out.canonical_qty_high = qty_low, qty_high
    provenance = low.provenance
    out.bridge_kind = provenance.bridge_kind
    if provenance.bridge_kind != "none":
        out.bridge_confirmed = not provenance.rests_on_unconfirmed

    if chosen is None and offer is None:
        out.status = "unpriced"
        return out
    out.status = "priced"
    out.offer = offer
    out.observation_id = chosen.observation.observation_id if chosen else None
    price = offer.unit_price if offer else chosen.norm_unit_price  # type: ignore[union-attr]
    assert product is not None  # noqa: S101
    pack_qty = offer.pack_qty if offer else _pack_canonical_qty(product, context)
    _price_line(out, price, pack_qty, qty_low, qty_high)
    return out


def _best_offer(
    offers: list[OfferPrice] | None, canonical_unit: str, pin_product_id: uuid.UUID | None
) -> OfferPrice | None:
    """This week's cheapest offer the line can take (X2): of the pinned product when
    there is a pin, in the ingredient's canonical unit."""
    usable = [
        o
        for o in offers or ()
        if o.unit == canonical_unit and (pin_product_id is None or o.product_id == pin_product_id)
    ]
    return min(usable, key=lambda o: (o.unit_price, str(o.offer_id))) if usable else None


def _price_line(
    out: LineCost,
    price: Decimal,
    pack_qty: Decimal | None,
    qty_low: Decimal,
    qty_high: Decimal | None,
) -> None:
    out.norm_unit_price = price
    out.consumed_cost = _money(_CTX.multiply(qty_low, price))
    out.basket_cost, out.packs = _basket(qty_low, price, pack_qty)
    if qty_high is not None:
        out.consumed_cost_high = _money(_CTX.multiply(qty_high, price))
        out.basket_cost_high, out.packs_high = _basket(qty_high, price, pack_qty)
