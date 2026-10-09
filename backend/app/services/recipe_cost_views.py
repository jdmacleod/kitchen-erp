"""Read models for recipe costing (07, 3D): a snapshot as the cost table shows it,
the committed history, and the list's cost column.

Line prices and converted quantities go through the unit-display layer, per lb,
oz, fl oz or each as the price book shows them (UI-3.10a); storage stays
metric. ``stale`` is decided at read time against ``stale_after_days``, as the
price book does, so an old price is never filtered out and never silently kept.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Ingredient, Recipe, RecipeCostLine, RecipeCostSnapshot, RecipeIngredient
from app.schemas.recipes import (
    CostCompleteness,
    CostHistoryItem,
    CostHistoryOut,
    CostLineOut,
    CostPriceUsed,
    CostQuantity,
    CostTotals,
    RecipeCostOut,
    RecipeCostSummary,
    RecipeIngredientOut,
)
from app.services import unit_display
from app.services.pricebook_views import stale_after_days
from app.services.recipe_costing import _pins
from app.units import display
from app.units.table import SEED_UNITS, units_by_code

_UNITS = units_by_code(SEED_UNITS)

# What each line's price and product read as: names, so the table needs no lookups.
_USED_SQL = """
    SELECT l.id AS line_id,
           o.observed_at, o.vendor_location_id AS location_id, vl.name AS location_name,
           v.id AS vendor_id, v.name AS vendor_name,
           p.id AS product_id, p.name AS product_name, p.brand
    FROM recipe_cost_line l
    LEFT JOIN price_observation o ON o.id = l.observation_id
    LEFT JOIN vendor_location vl ON vl.id = o.vendor_location_id
    LEFT JOIN vendor v ON v.id = vl.vendor_id
    LEFT JOIN product p ON p.id = l.product_id
    WHERE l.snapshot_id = CAST(:sid AS uuid)
"""


def _totals(row: RecipeCostSnapshot) -> CostTotals:
    return CostTotals(
        consumed_cost=row.consumed_cost,
        consumed_cost_high=row.consumed_cost_high,
        basket_cost=row.basket_cost,
        basket_cost_high=row.basket_cost_high,
        per_serving=row.per_serving,
    )


def display_qty(qty: Decimal | None, canonical_unit: str, mass: str | None) -> Decimal | None:
    """A canonical quantity restated in its display unit (2 places, 3 below 1)."""
    if qty is None:
        return None
    code = display.display_unit(canonical_unit, unit_display.system(), mass or "lb")
    if code is None:
        return None
    if code == "each":
        return display.round_price(qty)
    return display.round_price(qty / _UNITS[code].to_base_factor)


def _quantity(line: RecipeCostLine, mass: str | None) -> CostQuantity | None:
    if line.canonical_qty is None or line.canonical_unit is None:
        return None
    return CostQuantity(
        canonical_qty=line.canonical_qty,
        canonical_qty_high=line.canonical_qty_high,
        canonical_unit=line.canonical_unit,
        display_qty=display_qty(line.canonical_qty, line.canonical_unit, mass),
        display_qty_high=display_qty(line.canonical_qty_high, line.canonical_unit, mass),
        display_unit=unit_display.label(line.canonical_unit, mass),
    )


def _price(
    line: RecipeCostLine, used: dict[str, Any] | None, mass: str | None, now: datetime, stale: int
) -> CostPriceUsed | None:
    if line.norm_unit_price is None or line.canonical_unit is None:
        return None
    shown = unit_display.shown(line.norm_unit_price, line.canonical_unit, mass)
    used = used or {}
    observed_at = used.get("observed_at")
    return CostPriceUsed(
        norm_unit_price=line.norm_unit_price,
        norm_unit=line.canonical_unit,
        display_unit_price=shown.price if shown else None,
        display_unit=shown.unit if shown else None,
        product_id=used.get("product_id"),
        product_name=used.get("product_name"),
        brand=used.get("brand"),
        vendor_id=used.get("vendor_id"),
        vendor_name=used.get("vendor_name"),
        location_id=used.get("location_id"),
        location_name=used.get("location_name"),
        observation_id=line.observation_id,
        observed_at=observed_at,
        stale=observed_at is not None and (now - observed_at) > timedelta(days=stale),
    )


async def cost_out(db: AsyncSession, snapshot: RecipeCostSnapshot) -> RecipeCostOut:
    rows = (
        await db.execute(
            select(RecipeCostLine, RecipeIngredient)
            .join(RecipeIngredient, RecipeIngredient.id == RecipeCostLine.recipe_ingredient_id)
            .where(RecipeCostLine.snapshot_id == snapshot.id)
            .order_by(RecipeIngredient.seq)
        )
    ).all()
    used = {
        r["line_id"]: dict(r)
        for r in (await db.execute(text(_USED_SQL), {"sid": snapshot.id})).mappings()
    }
    ingredient_ids = {ri.ingredient_id for _, ri in rows if ri.ingredient_id is not None}
    names: dict[uuid.UUID, str] = {}
    if ingredient_ids:
        found = await db.execute(
            select(Ingredient.id, Ingredient.name).where(Ingredient.id.in_(ingredient_ids))
        )
        names = dict(found.all())
    masses = await unit_display.mass_units(db, ingredient_ids)
    pins = await _pins(db, snapshot.recipe_id)
    now = datetime.now(UTC)
    stale = stale_after_days()
    lines: list[CostLineOut] = []
    for line, ri in rows:
        mass = masses.get(ri.ingredient_id) if ri.ingredient_id else None
        lines.append(
            CostLineOut(
                id=line.id,
                line=RecipeIngredientOut.model_validate(ri).model_copy(
                    update={"ingredient_name": names.get(ri.ingredient_id)}
                ),
                yield_mode=ri.yield_mode,
                pinned=ri.name_norm in pins,
                status=line.status,
                failure_code=line.failure_code,
                quantity=_quantity(line, mass),
                yield_applied=line.yield_applied,
                yield_assumed=line.yield_applied is not None and line.yield_applied == 1,
                bridge_kind=line.bridge_kind,
                bridge_confirmed=line.bridge_confirmed,
                price=_price(line, used.get(line.id), mass, now, stale),
                consumed_cost=line.consumed_cost,
                consumed_cost_high=line.consumed_cost_high,
                basket_cost=line.basket_cost,
                basket_cost_high=line.basket_cost_high,
                packs=line.packs,
                packs_high=line.packs_high,
            )
        )
    return RecipeCostOut(
        id=snapshot.id,
        recipe_id=snapshot.recipe_id,
        basis=snapshot.basis,
        window_days=snapshot.window_days,
        min_quality=snapshot.min_quality,
        content_hash=snapshot.content_hash,
        head_commit=snapshot.head_commit,
        provisional=snapshot.provisional,
        computed_at=snapshot.computed_at,
        stale_after_days=stale,
        totals=_totals(snapshot),
        completeness=CostCompleteness(
            lines_total=snapshot.lines_total,
            lines_priced=snapshot.lines_priced,
            lines_unpriced=snapshot.lines_unpriced,
            lines_unconvertible=snapshot.lines_unconvertible,
            lines_unmapped=snapshot.lines_unmapped,
            lines_negligible=snapshot.lines_negligible,
        ),
        unconfirmed_share=snapshot.unconfirmed_share,
        lines=lines,
    )


async def history_out(db: AsyncSession, recipe: Recipe, basis: str) -> CostHistoryOut:
    """Committed (non-provisional) snapshots of the recipe under a basis, oldest first."""
    rows = (
        (
            await db.execute(
                select(RecipeCostSnapshot)
                .where(
                    RecipeCostSnapshot.recipe_id == recipe.id,
                    RecipeCostSnapshot.basis == basis,
                    RecipeCostSnapshot.provisional.is_(False),
                )
                .order_by(RecipeCostSnapshot.computed_at, RecipeCostSnapshot.id)
            )
        )
        .scalars()
        .all()
    )
    return CostHistoryOut(
        basis=basis,
        items=[
            CostHistoryItem(
                id=row.id,
                content_hash=row.content_hash,
                head_commit=row.head_commit,
                computed_at=row.computed_at,
                window_days=row.window_days,
                min_quality=row.min_quality,
                totals=_totals(row),
                lines_priced=row.lines_priced,
                lines_total=row.lines_total,
            )
            for row in rows
        ],
    )


async def summaries(db: AsyncSession, recipes: list[Recipe]) -> dict[uuid.UUID, RecipeCostSummary]:
    """Each recipe's default ``latest`` snapshot of its current content, when it has one."""
    if not recipes:
        return {}
    by_id = {r.id: r for r in recipes}
    rows = (
        (
            await db.execute(
                select(RecipeCostSnapshot).where(
                    RecipeCostSnapshot.recipe_id.in_(by_id),
                    RecipeCostSnapshot.basis == "latest",
                    RecipeCostSnapshot.window_days.is_(None),
                    RecipeCostSnapshot.min_quality.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    out: dict[uuid.UUID, RecipeCostSummary] = {}
    for row in rows:
        recipe = by_id[row.recipe_id]
        if row.content_hash != recipe.content_hash:
            continue
        out[row.recipe_id] = RecipeCostSummary(
            consumed_cost=row.consumed_cost,
            consumed_cost_high=row.consumed_cost_high,
            basket_cost=row.basket_cost,
            basket_cost_high=row.basket_cost_high,
            per_serving=row.per_serving,
            lines_priced=row.lines_priced,
            lines_total=row.lines_total,
            provisional=row.provisional,
            computed_at=row.computed_at,
        )
    return out
