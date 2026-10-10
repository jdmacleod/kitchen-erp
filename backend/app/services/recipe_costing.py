"""Recipe costing (07, 3D): snapshots under a stated basis, rebuildable like price_norm.

A snapshot costs one recipe at one content hash under one basis: ``latest``
(each line's most recent qualifying normalized price), ``average`` over a
window (default ``stale_after_days``), or ``cheapest``. Qualifying follows the
price book views exactly: a non-voided observation with a successful
normalization, from a committed purchase or a shelf price, never a posted
(listing) price, applying at an active location, of an active product that
fulfils the line's ingredient (merged products report under their survivor, as
``price_current`` does), of at least ``min_quality`` when given, and of the
pinned product when the line has a pin. A price older than ``stale_after_days``
is used and reported stale at read time, never filtered out.

Each line's quantity converts to the ingredient's canonical unit through
``convert`` with the pinned or chosen product's context: its density override
always, its pack only when the line is pinned and the canonical unit is a mass
or a volume ("1 each" of a pinned can is one can; "2 each" of an ingredient
counted in pieces is two pieces, never two packs). Yield follows the
heuristic in 05: a count or named-measure quantity is as purchased; a mass or
volume quantity is edible portion and is grossed up by dividing by
``yield_pct``; ``yield_mode`` overrides per line. An ingredient whose
``yield_pct`` is still 1 costs at 100% and the line says so (``yield_applied``
of 1 reads as "assumed").

Consumed cost is canonical quantity times unit price. Basket cost rounds the
line up to whole packs of the product used times that product's pack price
(unit price times the pack's canonical quantity); a product without a pack, or
whose pack cannot be expressed in the canonical unit, contributes its consumed
cost. Ranges carry a low and a high for both. Costs are quantized to four
places, ROUND_HALF_EVEN, the price book's rounding rule; an average unit price
to six, as ``price_norm`` stores one.

Statuses, in the order they are decided: ``negligible`` (no quantity, text
quantity, the negligible list, or a name marked not an ingredient),
``unmapped`` (no ingredient), ``unconvertible`` (``convert`` failed, with its
code), ``unpriced`` (nothing qualifies), ``priced``.

Recompute triggers (hash, alias, pin, bridge and price changes) are package
6b's; ``recompute_recipe`` and ``recompute_all`` are the entry points they call.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Context, Decimal
from typing import Literal

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.ids import new_id
from app.models import (
    Ingredient,
    Product,
    Recipe,
    RecipeCostLine,
    RecipeCostSnapshot,
    RecipeIngredient,
    RecipePin,
)
from app.services.pricebook_views import stale_after_days
from app.services.units import build_context
from app.units import ConversionContext, ConversionFailure, ProductContext, convert

Basis = Literal["latest", "average", "cheapest"]
BASES: tuple[Basis, ...] = ("latest", "average", "cheapest")

_CTX = Context(prec=28, rounding=ROUND_HALF_EVEN)
_FOUR = Decimal("0.0001")
_SIX = Decimal("0.000001")
_ONE = Decimal("1")


def _money(value: Decimal) -> Decimal:
    return value.quantize(_FOUR, rounding=ROUND_HALF_EVEN)


# --- qualifying prices ----------------------------------------------------------------

# One row per observation (offer_applicable repeats a chain price per location),
# under the price book's default rules: price_current (non-voided, no posted
# prices) applying at an active location, normalized, settled, active product.
_QUALIFYING_SQL = """
    SELECT DISTINCT ON (oa.observation_id)
           oa.observation_id, oa.product_id, p.ingredient_id, oa.observed_at,
           oa.norm_unit_price, oa.norm_unit
    FROM offer_applicable oa
    JOIN product p ON p.id = oa.product_id AND p.active
    LEFT JOIN purchase_line pl ON pl.id = oa.purchase_line_id
    LEFT JOIN purchase pu ON pu.id = pl.purchase_id
    WHERE p.ingredient_id = ANY(CAST(:ids AS uuid[]))
      AND oa.norm_unit_price IS NOT NULL
      AND (oa.purchase_line_id IS NULL OR pu.status = 'committed')
      AND (CAST(:min_quality AS integer) IS NULL
           OR coalesce(p.quality_rating, 0) >= CAST(:min_quality AS integer))
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
class Chosen:
    """The unit price a basis settled on, and the observation that stands for it."""

    norm_unit_price: Decimal
    observation: QualifyingPrice


async def qualifying_prices(
    db: AsyncSession, ingredient_ids: set[uuid.UUID], min_quality: int | None
) -> dict[uuid.UUID, list[QualifyingPrice]]:
    out: dict[uuid.UUID, list[QualifyingPrice]] = {}
    if not ingredient_ids:
        return out
    rows = await db.execute(
        text(_QUALIFYING_SQL),
        {"ids": [str(i) for i in ingredient_ids], "min_quality": min_quality},
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
    assert basis == "average" and window_days is not None
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


def _pack_canonical_qty(product: Product, context: ConversionContext) -> Decimal | None:
    """How much of the canonical unit one pack of the product holds, or None."""
    if product.pack_qty is None or product.pack_unit is None:
        return None
    if context.canonical_unit == "each" and product.pack_count:
        return Decimal(product.pack_count)
    inner = context.product
    without_pack = replace(
        context,
        measures=(),
        product=replace(inner, pack=None) if inner is not None else ProductContext(),
    )
    result = convert(product.pack_qty, product.pack_unit, without_pack)
    if isinstance(result, ConversionFailure) or result.qty <= 0:
        return None
    return result.qty


def _basket(
    qty: Decimal, unit_price: Decimal, pack_qty: Decimal | None
) -> tuple[Decimal, Decimal | None]:
    """Basket cost and packs for one quantity: whole packs times the pack price."""
    if pack_qty is None:
        return _money(_CTX.multiply(qty, unit_price)), None
    packs = _CTX.divide(qty, pack_qty).to_integral_value(rounding=ROUND_CEILING)
    pack_price = _money(_CTX.multiply(unit_price, pack_qty))
    return _money(_CTX.multiply(packs, pack_price)), packs


async def cost_line(
    db: AsyncSession,
    line: RecipeIngredient,
    ingredient: Ingredient | None,
    prices: list[QualifyingPrice],
    pin_product_id: uuid.UUID | None,
    basis: Basis,
    window_days: int | None,
    now: datetime,
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
    chosen = choose(candidates, basis, window_days, now)

    product_id = chosen.observation.product_id if chosen else pin_product_id
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
        out.failure_code = low.code
        return out
    high = None
    if line.qty_kind == "range" and line.qty_high is not None:
        high = convert(line.qty_high, from_unit, context)
        if isinstance(high, ConversionFailure):
            out.status = "unconvertible"
            out.failure_code = high.code
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

    if chosen is None:
        out.status = "unpriced"
        return out
    out.status = "priced"
    out.observation_id = chosen.observation.observation_id
    out.norm_unit_price = chosen.norm_unit_price
    assert product is not None
    pack_qty = _pack_canonical_qty(product, context)
    out.consumed_cost = _money(_CTX.multiply(qty_low, chosen.norm_unit_price))
    out.basket_cost, out.packs = _basket(qty_low, chosen.norm_unit_price, pack_qty)
    if qty_high is not None:
        out.consumed_cost_high = _money(_CTX.multiply(qty_high, chosen.norm_unit_price))
        out.basket_cost_high, out.packs_high = _basket(qty_high, chosen.norm_unit_price, pack_qty)
    return out


# --- the snapshot -----------------------------------------------------------------------


def effective_window(basis: Basis, window_days: int | None) -> int | None:
    """The window a snapshot is keyed on: only ``average`` has one."""
    if basis != "average":
        return None
    return window_days if window_days is not None else stale_after_days()


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


async def _pins(db: AsyncSession, recipe_id: uuid.UUID) -> dict[str, uuid.UUID]:
    rows = await db.execute(
        select(RecipePin.name_norm, RecipePin.product_id).where(RecipePin.recipe_id == recipe_id)
    )
    return dict(rows.all())


async def _find(
    db: AsyncSession,
    recipe: Recipe,
    basis: Basis,
    window_days: int | None,
    min_quality: int | None,
) -> RecipeCostSnapshot | None:
    return (
        await db.execute(
            select(RecipeCostSnapshot).where(
                RecipeCostSnapshot.recipe_id == recipe.id,
                RecipeCostSnapshot.content_hash == recipe.content_hash,
                RecipeCostSnapshot.basis == basis,
                RecipeCostSnapshot.window_days.is_not_distinct_from(window_days),
                RecipeCostSnapshot.min_quality.is_not_distinct_from(min_quality),
            )
        )
    ).scalar_one_or_none()


async def find_snapshot(
    db: AsyncSession,
    recipe: Recipe,
    basis: Basis = "latest",
    window_days: int | None = None,
    min_quality: int | None = None,
) -> RecipeCostSnapshot | None:
    """The snapshot for the recipe's current hash under this key, or None."""
    return await _find(db, recipe, basis, effective_window(basis, window_days), min_quality)


async def compute_snapshot(
    db: AsyncSession,
    recipe: Recipe,
    basis: Basis = "latest",
    window_days: int | None = None,
    min_quality: int | None = None,
    *,
    commit: bool = True,
) -> RecipeCostSnapshot:
    """Cost the recipe as it is indexed now and upsert the snapshot and its lines."""
    window = effective_window(basis, window_days)
    now = datetime.now(UTC)
    lines = (
        (
            await db.execute(
                select(RecipeIngredient)
                .where(RecipeIngredient.recipe_id == recipe.id)
                .order_by(RecipeIngredient.seq)
            )
        )
        .scalars()
        .all()
    )
    pins = await _pins(db, recipe.id)
    ingredients: dict[uuid.UUID, Ingredient | None] = {}
    for line in lines:
        if line.ingredient_id is not None and line.ingredient_id not in ingredients:
            ingredients[line.ingredient_id] = await _survivor(db, line.ingredient_id)
    prices = await qualifying_prices(
        db, {i.id for i in ingredients.values() if i is not None}, min_quality
    )

    costs: list[LineCost] = []
    for line in lines:
        ingredient = ingredients.get(line.ingredient_id) if line.ingredient_id else None
        costs.append(
            await cost_line(
                db,
                line,
                ingredient,
                prices.get(ingredient.id, []) if ingredient is not None else [],
                pins.get(line.name_norm),
                basis,
                window,
                now,
            )
        )

    priced = [c for c in costs if c.status == "priced"]
    consumed = consumed_high = basket = basket_high = per_serving = None
    share = Decimal(0)
    if priced:
        consumed = sum((c.consumed_cost for c in priced), Decimal(0))
        consumed_high = sum((c.consumed_cost_high or c.consumed_cost for c in priced), Decimal(0))
        basket = sum((c.basket_cost for c in priced), Decimal(0))
        basket_high = sum((c.basket_cost_high or c.basket_cost for c in priced), Decimal(0))
        if recipe.servings is not None and recipe.servings > 0:
            per_serving = _money(_CTX.divide(consumed, recipe.servings))
        if consumed > 0:
            unconfirmed = sum((c.consumed_cost for c in priced if c.unconfirmed), Decimal(0))
            share = _CTX.divide(unconfirmed, consumed).quantize(_FOUR, rounding=ROUND_HALF_EVEN)

    snapshot = await _find(db, recipe, basis, window, min_quality)
    if snapshot is None:
        snapshot = RecipeCostSnapshot(
            id=new_id(),
            recipe_id=recipe.id,
            content_hash=recipe.content_hash,
            basis=basis,
            window_days=window,
            min_quality=min_quality,
        )
        db.add(snapshot)
    else:
        await db.execute(delete(RecipeCostLine).where(RecipeCostLine.snapshot_id == snapshot.id))
    snapshot.head_commit = recipe.head_commit
    snapshot.provisional = recipe.dirty
    snapshot.consumed_cost = consumed
    snapshot.consumed_cost_high = consumed_high
    snapshot.basket_cost = basket
    snapshot.basket_cost_high = basket_high
    snapshot.per_serving = per_serving
    snapshot.lines_total = len(costs)
    snapshot.lines_priced = len(priced)
    snapshot.lines_unpriced = sum(c.status == "unpriced" for c in costs)
    snapshot.lines_unconvertible = sum(c.status == "unconvertible" for c in costs)
    snapshot.lines_unmapped = sum(c.status == "unmapped" for c in costs)
    snapshot.lines_negligible = sum(c.status == "negligible" for c in costs)
    snapshot.unconfirmed_share = share
    snapshot.computed_at = now
    await db.flush()
    for cost in costs:
        db.add(
            RecipeCostLine(
                id=new_id(),
                snapshot_id=snapshot.id,
                recipe_ingredient_id=cost.recipe_ingredient_id,
                status=cost.status,
                canonical_qty=cost.canonical_qty,
                canonical_qty_high=cost.canonical_qty_high,
                canonical_unit=cost.canonical_unit,
                yield_applied=cost.yield_applied,
                product_id=cost.product_id,
                observation_id=cost.observation_id,
                norm_unit_price=cost.norm_unit_price,
                consumed_cost=cost.consumed_cost,
                consumed_cost_high=cost.consumed_cost_high,
                basket_cost=cost.basket_cost,
                basket_cost_high=cost.basket_cost_high,
                packs=cost.packs,
                packs_high=cost.packs_high,
                bridge_kind=cost.bridge_kind,
                bridge_confirmed=cost.bridge_confirmed,
                failure_code=cost.failure_code,
            )
        )
    await db.flush()
    if commit:
        await db.commit()
    return snapshot


# --- recompute entry points (called by package 6b's triggers and the CLI) ---------------


async def snapshot_keys(
    db: AsyncSession, recipe_id: uuid.UUID
) -> list[tuple[Basis, int | None, int | None]]:
    """Every (basis, window_days, min_quality) the recipe has a snapshot under, plus
    the default ``latest`` key, which every recipe gets."""
    rows = await db.execute(
        select(
            RecipeCostSnapshot.basis, RecipeCostSnapshot.window_days, RecipeCostSnapshot.min_quality
        )
        .where(RecipeCostSnapshot.recipe_id == recipe_id)
        .distinct()
    )
    keys: list[tuple[Basis, int | None, int | None]] = [("latest", None, None)]
    for basis, window, quality in rows:
        key = (basis, window, quality)
        if key not in keys:
            keys.append(key)
    return keys


async def recompute_recipe(db: AsyncSession, recipe_id: uuid.UUID) -> int:
    """Recompute every snapshot key the recipe has (``latest`` at least). Commits."""
    recipe = await db.get(Recipe, recipe_id)
    if recipe is None:
        return 0
    keys = await snapshot_keys(db, recipe_id)
    for basis, window, quality in keys:
        await compute_snapshot(db, recipe, basis, window, quality, commit=False)
    await db.commit()
    return len(keys)


async def recompute_all(db: AsyncSession) -> int:
    """Truncate both cost tables and rebuild every recipe's ``latest`` snapshot (criterion 26)."""
    await db.execute(text("TRUNCATE recipe_cost_line, recipe_cost_snapshot"))
    await db.commit()
    recipes = (await db.execute(select(Recipe).order_by(Recipe.path))).scalars().all()
    for recipe in recipes:
        await compute_snapshot(db, recipe, "latest", commit=False)
    await db.commit()
    return len(recipes)
