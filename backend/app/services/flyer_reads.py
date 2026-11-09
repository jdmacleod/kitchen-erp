"""What the linked flyer offers say (18, 2U-4): this week's offers, what an offer
costs per unit, and a product's offer history.

```
flyer_offer_link (auto, confirmed) ─▶ newest run per source and ad week
    ─▶ current_offers(kitchen, on)   valid on that day; the later valid_from wins
    ─▶ offer_history(product)        every linked offer, with its ad week
    ─▶ offer_price(product, location, on)
         each, multi, bogo: the price of one, over its size
         per_lb, per_oz:    the printed rate
         percent_off, dollar_off: off the trailing regular price there
```

An offer with no size takes its product's size (``size_inherited``, U16); an offer
with neither has no unit price and is shown as printed. A person's correction on
the link (2W) wins over what was printed. No price view, receipt resolution or
default cost reads these (F4): they read the price book only for the base of a
percent or dollar off.
"""

from __future__ import annotations

import statistics
import uuid
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.models import Ingredient, Product
from app.services.price_rounding import unit_price
from app.services.units import build_context
from app.units import CanonicalQty, ConversionContext, convert

# Linked offers from the newest run of each source's ad week, with what a person
# corrected and the size they read by. A merged product reads as its survivor. An
# offer printed without a size reads by its product's as it is now, so a size added
# to the product after the offer linked counts too.
_LINKED = """
    WITH runs AS (
        SELECT DISTINCT ON (fr.ad_source_id, fr.ad_week_from) fr.id
        FROM flyer_run fr
        ORDER BY fr.ad_source_id, fr.ad_week_from, fr.created_at DESC
    )
    SELECT o.id, o.name_as_printed, o.price_basis,
           COALESCE(l.corrected_price, o.price) AS price,
           o.basis_qty, o.discount, o.requires_loyalty, o.limit_qty,
           o.valid_from, o.valid_to, fr.ad_week_from, fr.ad_week_to,
           (l.corrected_size_qty IS NULL AND o.size_qty IS NULL AND p.pack_qty IS NOT NULL)
               AS size_inherited,
           COALESCE(l.corrected_size_qty, o.size_qty, p.pack_qty) AS size_qty,
           CASE WHEN l.corrected_size_qty IS NOT NULL THEN l.corrected_size_unit
                WHEN o.size_qty IS NOT NULL THEN o.size_unit
                ELSE p.pack_unit END AS size_unit,
           p.id AS product_id, p.name AS product_name,
           r.id AS location_id, r.name AS location_name,
           v.id AS vendor_id, v.name AS vendor_name
    FROM flyer_offer o
    JOIN runs ON runs.id = o.flyer_run_id
    JOIN flyer_run fr ON fr.id = o.flyer_run_id
    JOIN flyer_offer_link l ON l.flyer_offer_id = o.id AND l.status IN ('auto', 'confirmed')
    JOIN product p0 ON p0.id = l.product_id
    JOIN product p ON p.id = COALESCE(p0.merged_into, p0.id)
    JOIN ad_source s ON s.id = fr.ad_source_id
    JOIN vendor_location r ON r.id = s.vendor_location_id
    JOIN vendor v ON v.id = r.vendor_id
"""

# The price book's regular prices for a product at a location: qualifying (a
# committed purchase or a shelf price; posted prices are not in offer_applicable),
# not on promotion, normalized, from the trailing window up to the day asked about.
_REGULAR = """
    SELECT oa.norm_unit, oa.norm_unit_price
    FROM offer_applicable oa
    LEFT JOIN purchase_line pl ON pl.id = oa.purchase_line_id
    LEFT JOIN purchase pu ON pu.id = pl.purchase_id
    WHERE oa.product_id = :product AND oa.applies_to_location_id = :location
      AND NOT oa.is_promo AND oa.norm_status = 'ok' AND oa.norm_unit_price IS NOT NULL
      AND oa.observed_at >= CAST(:since AS date) AND oa.observed_at < CAST(:until AS date)
      AND (oa.purchase_line_id IS NULL OR pu.status = 'committed')
"""

_PER = {"per_lb": "lb", "per_oz": "oz"}
_HUNDRED = Decimal(100)


@dataclass(frozen=True, slots=True)
class UnitPrice:
    """An offer's effective price per canonical unit, and the regular price it is off."""

    price: Decimal | None
    unit: str | None
    regular: Decimal | None


async def inherit_sizes(db: AsyncSession, offer_ids: list[uuid.UUID]) -> None:
    """Mark the linked offers printed without a size whose product has one (U16): the
    record of how it linked. The read models check the product's size as it is now."""
    await db.flush()
    await db.execute(
        text(
            """
            UPDATE flyer_offer_link l
            SET size_inherited = (o.size_qty IS NULL AND l.corrected_size_qty IS NULL
                                  AND p.pack_qty IS NOT NULL)
            FROM flyer_offer o, product p0, product p
            WHERE o.id = l.flyer_offer_id AND p0.id = l.product_id
              AND p.id = COALESCE(p0.merged_into, p0.id)
              AND l.flyer_offer_id = ANY(CAST(:ids AS uuid[]))
            """
        ),
        {"ids": offer_ids},
    )


async def current_offers(
    db: AsyncSession, home_base_id: uuid.UUID, on: date
) -> list[dict[str, Any]]:
    """The kitchen's linked offers valid on ``on`` (U9). Where two weeks' offers of one
    product overlap at a store, the one with the later ``valid_from`` wins."""
    rows = await db.execute(
        text(
            f"""
            SELECT DISTINCT ON (location_id, product_id) * FROM ({_LINKED}
                WHERE r.home_base_id = :kitchen
                  AND o.valid_from <= CAST(:on AS date) AND o.valid_to >= CAST(:on AS date)
            ) linked
            ORDER BY location_id, product_id, valid_from DESC, id
            """  # noqa: S608  constant fragments only
        ),
        {"kitchen": home_base_id, "on": on},
    )
    items = await _priced(db, [dict(r) for r in rows.mappings()], on)
    return sorted(
        items, key=lambda i: (i["vendor"]["name"], i["location"]["name"], i["product"]["name"])
    )


async def offer_history(
    db: AsyncSession, product_id: uuid.UUID, location_id: uuid.UUID | None = None
) -> list[dict[str, Any]]:
    """Every linked offer of a product, newest week first, at one store or all."""
    where = "WHERE p.id = :product" + (" AND r.id = :location" if location_id else "")
    rows = await db.execute(
        text(f"{_LINKED} {where} ORDER BY o.valid_from DESC, r.name, o.id"),
        {"product": product_id, "location": location_id},
    )
    return [_shape(dict(r)) for r in rows.mappings()]


async def offer_price(
    db: AsyncSession, product_id: uuid.UUID, location_id: uuid.UUID, on: date
) -> UnitPrice | None:
    """The effective unit price of the product's current offer at a store, if any."""
    rows = await db.execute(
        text(
            f"""
            {_LINKED}
            WHERE p.id = :product AND r.id = :location
              AND o.valid_from <= CAST(:on AS date) AND o.valid_to >= CAST(:on AS date)
            ORDER BY o.valid_from DESC, o.id LIMIT 1
            """
        ),
        {"product": product_id, "location": location_id, "on": on},
    )
    row = rows.mappings().first()
    if row is None:
        return None
    return await _unit_price(db, dict(row), await _context(db, product_id), on)


# --- pricing ---------------------------------------------------------------------------------


async def _context(db: AsyncSession, product_id: uuid.UUID) -> ConversionContext:
    product = await db.get(Product, product_id)
    assert product is not None  # noqa: S101
    ingredient = (
        await db.execute(
            select(Ingredient)
            .options(selectinload(Ingredient.measures))
            .where(Ingredient.id == product.ingredient_id)
        )
    ).scalar_one()
    return await build_context(db, ingredient, product)


def _canonical(
    qty: Decimal | None, unit: str | None, ctx: ConversionContext
) -> CanonicalQty | None:
    if qty is None or unit is None:
        return None
    result = convert(qty, unit, ctx)
    return result if isinstance(result, CanonicalQty) and result.qty > 0 else None


def _size(row: dict[str, Any], ctx: ConversionContext) -> CanonicalQty | None:
    """What one of the offer is. A size taken from the product is one pack of it. A
    printed size counts pieces, never packs: "6 ct" of a six-pack is the one pack,
    where "6 each" of a packed product would be six packs to the converter."""
    if row["size_inherited"]:
        return _canonical(Decimal(1), "each", ctx)
    if ctx.product is not None:
        ctx = replace(ctx, product=replace(ctx.product, pack=None, piece_pack=None))
    return _canonical(row["size_qty"], row["size_unit"], ctx)


async def _regular(
    db: AsyncSession, row: dict[str, Any], on: date, unit: str | None
) -> tuple[Decimal, str] | None:
    """The median regular unit price at the offer's store: in ``unit`` when the offer
    has one, else in the unit most of those prices are kept in."""
    since = on - timedelta(days=get_settings().stale_after_days)
    found = (
        await db.execute(
            text(_REGULAR),
            {
                "product": row["product_id"],
                "location": row["location_id"],
                "since": since,
                "until": on + timedelta(days=1),
            },
        )
    ).all()
    if unit is None and found:
        unit = Counter(u for u, _ in found).most_common(1)[0][0]
    prices = [Decimal(p) for u, p in found if u == unit]
    if not prices or unit is None:
        return None
    return unit_price(statistics.median(prices), Decimal(1)), unit


async def _unit_price(
    db: AsyncSession, row: dict[str, Any], ctx: ConversionContext, on: date
) -> UnitPrice:
    basis, price = row["price_basis"], row["price"]
    if basis in _PER:
        one = _canonical(Decimal(1), _PER[basis], ctx)
        if one is None or price is None:
            return UnitPrice(None, None, None)
        return UnitPrice(unit_price(price, one.qty), one.unit, None)
    size = _size(row, ctx)
    if basis in ("each", "multi", "bogo"):
        if size is None or price is None:
            return UnitPrice(None, None, None)
        each = {"each": price, "multi": price / (row["basis_qty"] or 1), "bogo": price / 2}[basis]
        return UnitPrice(unit_price(each, size.qty), size.unit, None)
    regular = await _regular(db, row, on, size.unit if size else None)
    discount = row["discount"]
    if regular is None or discount is None:
        return UnitPrice(None, None, None)
    base, unit = regular
    if basis == "percent_off":
        off = unit_price(base * (_HUNDRED - discount) / _HUNDRED, Decimal(1))
        return UnitPrice(off if off > 0 else None, unit, base)
    if size is None:  # dollar_off: a dollar amount off a pack needs the pack's size
        return UnitPrice(None, None, base)
    pack = base * size.qty - discount
    return UnitPrice(unit_price(pack, size.qty) if pack > 0 else None, unit, base)


async def _priced(db: AsyncSession, rows: list[dict[str, Any]], on: date) -> list[dict[str, Any]]:
    contexts: dict[uuid.UUID, ConversionContext] = {}
    items = []
    for row in rows:
        product_id = row["product_id"]
        if product_id not in contexts:
            contexts[product_id] = await _context(db, product_id)
        priced = await _unit_price(db, row, contexts[product_id], on)
        items.append(
            _shape(row)
            | {
                "unit_price": priced.price,
                "unit": priced.unit,
                "regular_unit_price": priced.regular,
            }
        )
    return items


def _shape(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "product": {"id": row["product_id"], "name": row["product_name"]},
        "vendor": {"id": row["vendor_id"], "name": row["vendor_name"]},
        "location": {"id": row["location_id"], "name": row["location_name"]},
        **{
            k: row[k]
            for k in (
                "name_as_printed",
                "price_basis",
                "price",
                "basis_qty",
                "discount",
                "size_qty",
                "size_unit",
                "size_inherited",
                "requires_loyalty",
                "limit_qty",
                "valid_from",
                "valid_to",
                "ad_week_from",
                "ad_week_to",
            )
        },
    }
