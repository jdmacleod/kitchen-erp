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
(unit price times the pack's canonical quantity; ``services/packs``, shared with
the trip planner); a product without a pack, or whose pack cannot be expressed
in the canonical unit, contributes its consumed cost. Ranges carry a low and a
high for both. Costs are quantized to four places, ROUND_HALF_EVEN, the price
book's rounding rule; an average unit price to six, as ``price_norm`` stores one.

Statuses, in the order they are decided: ``negligible`` (no quantity, text
quantity, the negligible list, or a name marked not an ingredient),
``unmapped`` (no ingredient), ``unconvertible`` (``convert`` failed, with its
code), ``unpriced`` (nothing qualifies), ``priced``.

The recompute triggers (hash, alias, pin, bridge and price changes) live in
``services/recipe_cost_triggers``; ``recompute_recipe_core`` is the entry point
they call inside a caller's transaction, ``recompute_all`` the CLI's.

Qualifying prices, the price a basis chooses and each line's cost live in
``services/recipe_cost_lines``; this module assembles them into a recipe.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal

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
from app.models.geo import VendorLocation
from app.services.packs import CTX as _CTX
from app.services.packs import money as _money
from app.services.pricebook_views import stale_after_days
from app.services.recipe_cost_lines import (
    _FOUR,
    BASES,
    Basis,
    LineCost,
    OfferPrice,
    cost_line,
    qualifying_prices,
)

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


@dataclass(slots=True)
class Costing:
    """A recipe costed in memory: every line's cost and the totals, before (or
    without) a snapshot row. ``compute_snapshot`` writes one; ``compute_view``
    reads one straight out for the per-kitchen view (17, 4A, L3)."""

    lines: list[RecipeIngredient]
    costs: list[LineCost]
    computed_at: datetime
    consumed_cost: Decimal | None = None
    consumed_cost_high: Decimal | None = None
    basket_cost: Decimal | None = None
    basket_cost_high: Decimal | None = None
    per_serving: Decimal | None = None
    unconfirmed_share: Decimal = Decimal(0)

    @property
    def priced(self) -> list[LineCost]:
        return [c for c in self.costs if c.status == "priced"]

    def count(self, status: str) -> int:
        return sum(c.status == status for c in self.costs)


async def kitchen_offers(
    db: AsyncSession, home_base_id: uuid.UUID, min_quality: int | None
) -> dict[uuid.UUID, list[OfferPrice]]:
    """The offers valid today at the kitchen's stores, by the ingredient of their
    product: active products only, of at least ``min_quality`` when given."""
    # Imported here: flyer reads import the catalog, which imports this module.
    from app.services import flyer_reads, flyers

    location_ids = list(
        (
            await db.execute(
                select(VendorLocation.id).where(
                    VendorLocation.home_base_id == home_base_id, VendorLocation.active
                )
            )
        ).scalars()
    )
    found = await flyer_reads.stand_ins(db, location_ids, flyers.today())
    if not found:
        return {}
    products = {
        p.id: p
        for p in (
            await db.execute(select(Product).where(Product.id.in_({k[0] for k in found})))
        ).scalars()
    }
    out: dict[uuid.UUID, list[OfferPrice]] = {}
    for (product_id, location_id), stand_in in found.items():
        product = products.get(product_id)
        if product is None or not product.active:
            continue
        if min_quality is not None and (product.quality_rating or 0) < min_quality:
            continue
        out.setdefault(product.ingredient_id, []).append(
            OfferPrice(
                product_id=product_id,
                location_id=location_id,
                offer_id=stand_in.offer_id,
                unit_price=stand_in.unit_price,
                unit=stand_in.unit,
                estimate=stand_in.estimate,
                pack_qty=stand_in.pack_qty,
            )
        )
    return out


async def cost_recipe(
    db: AsyncSession,
    recipe: Recipe,
    basis: Basis,
    window: int | None,
    min_quality: int | None,
    *,
    home_base_id: uuid.UUID | None = None,
) -> Costing:
    """Cost the recipe as it is indexed now; nothing is written."""
    now = datetime.now(UTC)
    # Read from the database, not the identity map: a trigger runs after a bulk
    # UPDATE (a decision, a merge) that the session's loaded rows do not reflect.
    lines = (
        (
            await db.execute(
                select(RecipeIngredient)
                .where(RecipeIngredient.recipe_id == recipe.id)
                .order_by(RecipeIngredient.seq)
                .execution_options(populate_existing=True)
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
        db,
        {i.id for i in ingredients.values() if i is not None},
        min_quality,
        home_base_id,
    )
    offers: dict[uuid.UUID, list[OfferPrice]] = {}
    if basis == "offers":
        assert home_base_id is not None  # noqa: S101  the API refuses it without a kitchen
        offers = await kitchen_offers(db, home_base_id, min_quality)

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
                offers.get(ingredient.id, []) if ingredient is not None else None,
            )
        )

    out = Costing(lines=list(lines), costs=costs, computed_at=now)
    priced = out.priced
    if priced:
        consumed = sum((c.consumed_cost for c in priced), Decimal(0))
        out.consumed_cost = consumed
        out.consumed_cost_high = sum(
            (c.consumed_cost_high or c.consumed_cost for c in priced), Decimal(0)
        )
        out.basket_cost = sum((c.basket_cost for c in priced), Decimal(0))
        out.basket_cost_high = sum(
            (c.basket_cost_high or c.basket_cost for c in priced), Decimal(0)
        )
        if recipe.servings is not None and recipe.servings > 0:
            out.per_serving = _money(_CTX.divide(consumed, recipe.servings))
        if consumed > 0:
            unconfirmed = sum((c.consumed_cost for c in priced if c.unconfirmed), Decimal(0))
            out.unconfirmed_share = _CTX.divide(unconfirmed, consumed).quantize(
                _FOUR, rounding=ROUND_HALF_EVEN
            )
    return out


def _fill_snapshot(snapshot: RecipeCostSnapshot, recipe: Recipe, costing: Costing) -> None:
    snapshot.head_commit = recipe.head_commit
    snapshot.provisional = recipe.dirty
    snapshot.consumed_cost = costing.consumed_cost
    snapshot.consumed_cost_high = costing.consumed_cost_high
    snapshot.basket_cost = costing.basket_cost
    snapshot.basket_cost_high = costing.basket_cost_high
    snapshot.per_serving = costing.per_serving
    snapshot.lines_total = len(costing.costs)
    snapshot.lines_priced = len(costing.priced)
    snapshot.lines_unpriced = costing.count("unpriced")
    snapshot.lines_unconvertible = costing.count("unconvertible")
    snapshot.lines_unmapped = costing.count("unmapped")
    snapshot.lines_negligible = costing.count("negligible")
    snapshot.unconfirmed_share = costing.unconfirmed_share
    snapshot.computed_at = costing.computed_at


def _line_row(snapshot_id: uuid.UUID, cost: LineCost) -> RecipeCostLine:
    return RecipeCostLine(
        id=new_id(),
        snapshot_id=snapshot_id,
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
    assert basis in BASES  # noqa: S101  "offers" is a per-kitchen view, never kept
    window = effective_window(basis, window_days)
    costing = await cost_recipe(db, recipe, basis, window, min_quality)
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
    _fill_snapshot(snapshot, recipe, costing)
    await db.flush()
    for cost in costing.costs:
        db.add(_line_row(snapshot.id, cost))
    await db.flush()
    if commit:
        await db.commit()
    return snapshot


async def compute_view(
    db: AsyncSession,
    recipe: Recipe,
    basis: Basis = "latest",
    window_days: int | None = None,
    min_quality: int | None = None,
    *,
    home_base_id: uuid.UUID,
) -> tuple[
    RecipeCostSnapshot,
    list[tuple[RecipeCostLine, RecipeIngredient]],
    dict[uuid.UUID, OfferPrice],
]:
    """The recipe costed with prices at one kitchen's locations only, as an
    unsaved snapshot, its unsaved lines and, for the "offers" basis, the offer that
    priced each line an offer priced, by the line's id (17, 4A, L3; 18, X2).

    Nothing here is added to the session: the stored snapshot, ``provisional``
    and the household-wide history are untouched, and the row ids are fresh on
    every call.
    """
    window = effective_window(basis, window_days)
    costing = await cost_recipe(db, recipe, basis, window, min_quality, home_base_id=home_base_id)
    snapshot = RecipeCostSnapshot(
        id=new_id(),
        recipe_id=recipe.id,
        content_hash=recipe.content_hash,
        basis=basis,
        window_days=window,
        min_quality=min_quality,
    )
    _fill_snapshot(snapshot, recipe, costing)
    by_id = {line.id: line for line in costing.lines}
    rows = [(_line_row(snapshot.id, c), by_id[c.recipe_ingredient_id]) for c in costing.costs]
    offered = {row.id: c.offer for (row, _), c in zip(rows, costing.costs, strict=True) if c.offer}
    return snapshot, rows, offered


# --- recompute entry points (called by the triggers and the CLI) ------------------------


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


async def recompute_recipe_core(db: AsyncSession, recipe_id: uuid.UUID) -> int:
    """Recompute every snapshot key the recipe has (``latest`` at least). Flushes,
    never commits, so a trigger can make it part of the caller's transaction."""
    recipe = await db.get(Recipe, recipe_id)
    if recipe is None:
        return 0
    keys = await snapshot_keys(db, recipe_id)
    for basis, window, quality in keys:
        await compute_snapshot(db, recipe, basis, window, quality, commit=False)
    return len(keys)


async def recompute_recipe(db: AsyncSession, recipe_id: uuid.UUID) -> int:
    """The same, committed."""
    n = await recompute_recipe_core(db, recipe_id)
    await db.commit()
    return n


async def recompute_all(db: AsyncSession) -> int:
    """Truncate both cost tables and rebuild every recipe's ``latest`` snapshot (criterion 26)."""
    await db.execute(text("TRUNCATE recipe_cost_line, recipe_cost_snapshot"))
    await db.commit()
    recipes = (await db.execute(select(Recipe).order_by(Recipe.path))).scalars().all()
    for recipe in recipes:
        await compute_snapshot(db, recipe, "latest", commit=False)
    await db.commit()
    return len(recipes)
