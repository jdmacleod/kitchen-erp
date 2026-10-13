"""What each list item would cost at each candidate location (17, 4B).

Per item per candidate, the cheapest qualifying product. Qualifying is the price
book's rule as 07 states it, keyed by location: the latest non-voided, normalized
price per (product, location) from ``offer_applicable``, so a chain price
applies at every active location of the chain and a location-scoped price at
its own only; from a committed purchase or a shelf price, never a posted
(listing) price (the view without ``_all`` already leaves those out); of an
active product. ``recipe_costing.qualifying_prices`` is not reused: its
``DISTINCT ON (observation_id)`` collapses a chain price to one row on purpose,
and the planner needs one per location.

An ingredient item takes any active product of its ingredient (merges
followed) of at least ``min_quality``; a product item takes that product only.
Each of the item's (amount, unit) rows converts to the product's canonical unit
through ``convert`` with the product's context. A product item is one a person
named, so "1 each" of it is one pack; an ingredient item's "2 each" is two
pieces, never two packs, and a count the product cannot express is rounded by
``pack_count`` when it states one. Packs round up through ``services/packs``;
without a pack size the cost is quantity times unit price with ``packs`` null;
a row that fails to convert makes that product unusable at that location
(``cant_convert``). A price older than ``stale_after_days`` is used and marked.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from typing import Literal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings
from app.models import Ingredient, Product
from app.models.plan import ShoppingListItem
from app.services import packs
from app.services.units import load_units
from app.units import (
    ConversionContext,
    ConversionFailure,
    Measure,
    Pack,
    ProductContext,
    convert,
)

Reason = Literal["no_offer", "cant_convert", "quality"]

# The latest qualifying price per (product, location): offer_latest's rule with
# the committed-purchase restriction recipe costing applies.
_OFFERS_SQL = """
    SELECT DISTINCT ON (oa.product_id, oa.applies_to_location_id)
           oa.observation_id, oa.product_id, oa.applies_to_location_id AS location_id,
           oa.observed_at, oa.norm_unit_price, oa.norm_unit
    FROM offer_applicable oa
    JOIN product p ON p.id = oa.product_id AND p.active
    LEFT JOIN purchase_line pl ON pl.id = oa.purchase_line_id
    LEFT JOIN purchase pu ON pu.id = pl.purchase_id
    WHERE oa.applies_to_location_id = ANY(CAST(:location_ids AS uuid[]))
      AND (p.ingredient_id = ANY(CAST(:ingredient_ids AS uuid[]))
           OR p.id = ANY(CAST(:product_ids AS uuid[])))
      AND oa.norm_unit_price IS NOT NULL
      AND (oa.purchase_line_id IS NULL OR pu.status = 'committed')
    ORDER BY oa.product_id, oa.applies_to_location_id, oa.observed_at DESC, oa.observation_id DESC
"""


@dataclass(frozen=True, slots=True)
class Price:
    """One product's latest qualifying price at one location."""

    observation_id: uuid.UUID
    product_id: uuid.UUID
    location_id: uuid.UUID
    observed_at: datetime
    unit_price: Decimal
    unit: str


@dataclass(frozen=True, slots=True)
class Offer:
    """The cheapest way to fill one item at one location."""

    item_id: uuid.UUID
    location_id: uuid.UUID
    product_id: uuid.UUID
    observation_id: uuid.UUID
    observed_at: datetime
    unit_price: Decimal
    packs: Decimal | None
    pack_price: Decimal | None
    line_cost: Decimal
    stale: bool


@dataclass(frozen=True, slots=True)
class OfferBook:
    """Offers by (item, location), and why the pairs without one have none."""

    offers: dict[tuple[uuid.UUID, uuid.UUID], Offer]
    reasons: dict[tuple[uuid.UUID, uuid.UUID], Reason]

    def for_item(self, item_id: uuid.UUID) -> list[Offer]:
        return [o for (i, _), o in self.offers.items() if i == item_id]


# --- loading ---------------------------------------------------------------------------------


async def _survivor(db: AsyncSession, ingredient_id: uuid.UUID) -> Ingredient | None:
    stmt = select(Ingredient).options(selectinload(Ingredient.measures))
    ingredient = (await db.execute(stmt.where(Ingredient.id == ingredient_id))).scalar_one_or_none()
    for _ in range(8):
        if ingredient is None or ingredient.merged_into is None:
            break
        ingredient = (
            await db.execute(stmt.where(Ingredient.id == ingredient.merged_into))
        ).scalar_one_or_none()
    return ingredient


async def qualifying_prices(
    db: AsyncSession,
    location_ids: list[uuid.UUID],
    ingredient_ids: set[uuid.UUID],
    product_ids: set[uuid.UUID],
) -> list[Price]:
    if not location_ids or not (ingredient_ids or product_ids):
        return []
    rows = await db.execute(
        text(_OFFERS_SQL),
        {
            "location_ids": [str(i) for i in location_ids],
            "ingredient_ids": [str(i) for i in ingredient_ids],
            "product_ids": [str(i) for i in product_ids],
        },
    )
    return [
        Price(
            observation_id=r["observation_id"],
            product_id=r["product_id"],
            location_id=r["location_id"],
            observed_at=r["observed_at"],
            unit_price=Decimal(str(r["norm_unit_price"])),
            unit=r["norm_unit"],
        )
        for r in rows.mappings()
    ]


# --- costing one item with one product ------------------------------------------------------


def _context(
    ingredient: Ingredient, product: Product, units, *, keep_pack: bool
) -> ConversionContext:
    pack = None
    if keep_pack and product.pack_qty is not None and product.pack_unit is not None:
        pack = Pack(product.pack_qty, product.pack_unit, product.pack_count)
    return ConversionContext(
        canonical_unit=ingredient.canonical_unit,
        density_g_per_ml=ingredient.density_g_per_ml,
        density_source=ingredient.density_source,
        density_confirmed=ingredient.density_confirmed,
        measures=tuple(
            Measure(m.label, m.canonical_qty, m.source, m.confirmed) for m in ingredient.measures
        ),
        product=ProductContext(
            pack=pack,
            density_g_per_ml=product.density_override,
            density_source=product.density_override_source,
            density_confirmed=product.density_override_confirmed,
        ),
        units=units,
    )


@dataclass(frozen=True, slots=True)
class Costed:
    packs: Decimal | None
    pack_price: Decimal | None
    line_cost: Decimal


def amounts_in_canonical(
    pairs: list[tuple[Decimal, str]],
    kind: str,
    ingredient: Ingredient,
    product: Product,
    units,
) -> tuple[Decimal, Decimal] | None:
    """An item's amounts as (canonical quantity, loose pieces) for one product,
    or None when a pair cannot convert (``cant_convert``).

    Pieces are counts the product cannot express in its canonical unit and
    rounds by ``pack_count`` instead."""
    # A pack stands for "1 each" only of a product a person named, and never
    # when the canonical unit is itself a count (one pack is then pack_count pieces).
    keep_pack = kind == "product" and ingredient.canonical_unit != "each"
    context = _context(ingredient, product, units, keep_pack=keep_pack)
    canonical = Decimal(0)
    pieces = Decimal(0)
    for amount, unit in pairs:
        result = convert(amount, unit, context)
        if not isinstance(result, ConversionFailure):
            canonical += result.qty
            continue
        unit_row = units.get(unit)
        if unit_row is not None and unit_row.dimension == "count" and product.pack_count:
            pieces += packs.CTX.multiply(amount, unit_row.to_base_factor)
            continue
        return None
    return canonical, pieces


def cost_item(
    pairs: list[tuple[Decimal, str]],
    kind: str,
    ingredient: Ingredient,
    product: Product,
    unit_price: Decimal,
    units,
) -> Costed | None:
    """Packs and cost for an item's amounts with one product, or None (``cant_convert``)."""
    converted = amounts_in_canonical(pairs, kind, ingredient, product, units)
    if converted is None:
        return None
    canonical, pieces = converted
    pack_qty = packs.pack_canonical_qty(
        product, _context(ingredient, product, units, keep_pack=True)
    )
    if pack_qty is None:
        if pieces > 0:
            return None
        return Costed(None, None, packs.money(packs.CTX.multiply(canonical, unit_price)))
    whole = packs.packs_for(canonical, pack_qty) if canonical > 0 else Decimal(0)
    if pieces > 0:
        if not product.pack_count:
            return None
        whole += packs.CTX.divide(pieces, Decimal(product.pack_count)).to_integral_value(
            rounding=ROUND_CEILING
        )
    price = packs.pack_price(unit_price, pack_qty)
    return Costed(whole, price, packs.money(packs.CTX.multiply(whole, price)))


# --- the book --------------------------------------------------------------------------------


def _qualifies(item: ShoppingListItem, product: Product) -> bool:
    if item.kind == "product":
        return product.id == item.product_id
    if item.min_quality is None:
        return True
    return (product.quality_rating or 0) >= item.min_quality


async def offers(
    db: AsyncSession,
    items: list[ShoppingListItem],
    location_ids: list[uuid.UUID],
    settings: Settings,
    *,
    now: datetime | None = None,
) -> OfferBook:
    """The cheapest qualifying product per (item, location), with packs and cost."""
    now = now or datetime.now(UTC)
    stale_before = now - timedelta(days=settings.stale_after_days)
    units = await load_units(db)

    # Ingredients as they resolve today (merges followed), keyed by the id asked for.
    resolved: dict[uuid.UUID, Ingredient | None] = {}
    product_ids: set[uuid.UUID] = set()
    for item in items:
        if item.kind == "product" and item.product_id is not None:
            product_ids.add(item.product_id)
        elif item.ingredient_id is not None and item.ingredient_id not in resolved:
            resolved[item.ingredient_id] = await _survivor(db, item.ingredient_id)
    survivors = {i.id for i in resolved.values() if i is not None}
    prices = await qualifying_prices(db, location_ids, survivors, product_ids)

    wanted = {p.product_id for p in prices}
    products: dict[uuid.UUID, Product] = {}
    if wanted:
        rows = (await db.execute(select(Product).where(Product.id.in_(wanted)))).scalars()
        products = {p.id: p for p in rows}
    for product in products.values():  # a product item's ingredient, for its context
        if product.ingredient_id not in resolved:
            resolved[product.ingredient_id] = await _survivor(db, product.ingredient_id)

    by_location: dict[uuid.UUID, list[Price]] = {}
    for price in prices:
        by_location.setdefault(price.location_id, []).append(price)

    found: dict[tuple[uuid.UUID, uuid.UUID], Offer] = {}
    reasons: dict[tuple[uuid.UUID, uuid.UUID], Reason] = {}
    for item in items:
        pairs = [(a.amount, a.unit) for a in item.amounts]
        target = resolved.get(item.ingredient_id) if item.kind == "ingredient" else None
        for location_id in location_ids:
            best: tuple[tuple[Decimal, Decimal, str], Offer] | None = None
            reason: Reason = "no_offer"
            for price in by_location.get(location_id, ()):
                product = products.get(price.product_id)
                if product is None:
                    continue
                if item.kind == "ingredient":
                    if target is None or product.ingredient_id != target.id:
                        continue
                    ingredient = target
                else:
                    if product.id != item.product_id:
                        continue
                    ingredient = resolved.get(product.ingredient_id)
                    if ingredient is None:
                        continue
                if not _qualifies(item, product):
                    reason = "quality" if reason == "no_offer" else reason
                    continue
                if price.unit != ingredient.canonical_unit:
                    continue
                costed = cost_item(pairs, item.kind, ingredient, product, price.unit_price, units)
                if costed is None:
                    reason = "cant_convert"
                    continue
                offer = Offer(
                    item_id=item.id,
                    location_id=location_id,
                    product_id=product.id,
                    observation_id=price.observation_id,
                    observed_at=price.observed_at,
                    unit_price=price.unit_price,
                    packs=costed.packs,
                    pack_price=costed.pack_price,
                    line_cost=costed.line_cost,
                    stale=price.observed_at < stale_before,
                )
                key = (costed.line_cost, price.unit_price, str(product.id))
                if best is None or key < best[0]:
                    best = (key, offer)
            if best is not None:
                found[(item.id, location_id)] = best[1]
            else:
                reasons[(item.id, location_id)] = reason
    return OfferBook(offers=found, reasons=reasons)


def without_stale(book: OfferBook) -> OfferBook:
    """The same book with every stale offer removed (for the stale-dependence check)."""
    kept = {k: o for k, o in book.offers.items() if not o.stale}
    reasons = dict(book.reasons)
    for k in book.offers.keys() - kept.keys():
        reasons[k] = "no_offer"
    return replace(book, offers=kept, reasons=reasons)
