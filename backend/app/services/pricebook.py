"""The price book: append-only observations, voids, and rebuildable normalization.

Rounding rule (the only one in the price book): arithmetic in a 28-digit Decimal
context; the normalized unit price is quantized to six places, ROUND_HALF_EVEN.

Recipe cost snapshots (07, 3D) are derived from these prices, so the paths that
change what qualifies, or how a price normalizes, hand the change on to
``recipe_cost_triggers`` in the same transaction; with no recipe indexed those
calls are one empty query each.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.core.errors import ApiError
from app.models import (
    AppUser,
    Ingredient,
    PriceNorm,
    PriceObservation,
    PriceObservationVoid,
    Product,
)
from app.models.units import UnitRow
from app.services import recipe_cost_triggers
from app.services.pagination import decode_cursor, encode_cursor
from app.services.units import build_context
from app.units import CanonicalQty, ConversionFailure, convert

_CTX = Context(prec=28, rounding=ROUND_HALF_EVEN)
_SIX = Decimal("0.000001")


def unit_price(price: Decimal, canonical_qty: Decimal) -> Decimal:
    return _CTX.divide(price, canonical_qty).quantize(_SIX, rounding=ROUND_HALF_EVEN)


# --- normalization ----------------------------------------------------------


def observed_as(product_ids):
    """Observation product ids reported under ``product_ids``: themselves and every
    product merged into one of them (#179; merges are one level deep)."""
    return select(Product.id).where(
        or_(Product.id.in_(product_ids), Product.merged_into.in_(product_ids))
    )


async def _norm_row(db: AsyncSession, observation: PriceObservation) -> PriceNorm:
    product = await db.get(Product, observation.product_id)
    assert product is not None
    if product.merged_into is not None:
        # A merged product's prices compare as its survivor's: its pack, its density.
        product = await db.get(Product, product.merged_into)
        assert product is not None
    ingredient = (
        await db.execute(
            select(Ingredient)
            .options(selectinload(Ingredient.measures))
            .where(Ingredient.id == product.ingredient_id)
        )
    ).scalar_one()
    context = await build_context(db, ingredient, product)
    result = convert(observation.qty, observation.unit, context)
    if isinstance(result, CanonicalQty):
        return PriceNorm(
            observation_id=observation.id,
            canonical_qty=result.qty,
            norm_unit=result.unit,
            norm_unit_price=unit_price(observation.price, result.qty),
            status="ok",
            bridge_kind=result.provenance.bridge_kind,
            bridge_source=result.provenance.source,
            bridge_confirmed=result.provenance.confirmed,
            convert_version=result.version,
        )
    assert isinstance(result, ConversionFailure)
    return PriceNorm(
        observation_id=observation.id,
        status=result.code,
        bridge_kind="none",
        convert_version=result.version,
    )


async def normalize_core(db: AsyncSession, observation_ids: list[uuid.UUID]) -> int:
    """Recompute price_norm for the given observations. Flushes, never commits,
    so a caller such as an ingredient merge can make it part of one transaction."""
    if not observation_ids:
        return 0
    await db.execute(delete(PriceNorm).where(PriceNorm.observation_id.in_(observation_ids)))
    rows = (
        (await db.execute(select(PriceObservation).where(PriceObservation.id.in_(observation_ids))))
        .unique()
        .scalars()
    )
    count = 0
    for observation in rows:
        db.add(await _norm_row(db, observation))
        count += 1
    await db.flush()
    return count


async def normalize(db: AsyncSession, observation_ids: list[uuid.UUID]) -> int:
    """Recompute price_norm for the given observations. Commits."""
    count = await normalize_core(db, observation_ids)
    # The snapshots that used these prices follow them (3D); a full rebuild
    # (``recompute_all``) reaches every recipe with a priced line.
    await recipe_cost_triggers.after_prices_changed(db, observation_ids)
    await db.commit()
    return count


async def recompute_all(db: AsyncSession) -> int:
    """Truncate and rebuild the whole table."""
    await db.execute(text("TRUNCATE price_norm"))
    await db.commit()
    ids = list((await db.execute(select(PriceObservation.id))).scalars())
    return await normalize(db, ids)


async def _dependents(db: AsyncSession, where) -> list[uuid.UUID]:
    stmt = (
        select(PriceObservation.id)
        .join(PriceNorm, PriceNorm.observation_id == PriceObservation.id, isouter=True)
        .where(where)
    )
    return list((await db.execute(stmt)).scalars())


async def recompute_for_ingredient(
    db: AsyncSession,
    ingredient_id: uuid.UUID,
    *,
    canonical_unit_changed: bool = False,
    commit: bool = True,
) -> int:
    """After a density or named-measure change: every observation of the
    ingredient's products that crossed a bridge, failed to, or crossed a pack
    (a pack may itself have crossed the density). Same-dimension observations
    depend on nothing and are left alone.

    After a canonical-unit change nothing is left alone: an observation that
    needed no bridge in the old unit may need one in the new unit, and its
    stored price is per the old unit.

    With ``commit=False`` it only flushes (the merge's single commit, O2)."""
    product_ids = select(Product.id).where(Product.ingredient_id == ingredient_id)
    where = PriceObservation.product_id.in_(observed_as(product_ids))
    if not canonical_unit_changed:
        where = where & (
            PriceNorm.observation_id.is_(None)
            | (PriceNorm.status != "ok")
            | (PriceNorm.bridge_kind != "none")
        )
    ids = await _dependents(db, where)
    n = await normalize_core(db, ids)
    # Recipe lines of the ingredient crossed the same bridge, whichever product
    # priced them (3D, criterion 25); the snapshots follow the prices.
    await recipe_cost_triggers.after_ingredient_bridge_changed(db, ingredient_id)
    if commit:
        await db.commit()
    return n


async def recompute_for_product(db: AsyncSession, product_id: uuid.UUID) -> int:
    """After a pack or density-override change on a product."""
    n = await recompute_for_product_core(db, product_id)
    await db.commit()
    return n


async def recompute_for_product_core(db: AsyncSession, product_id: uuid.UUID) -> int:
    """The same inside a caller's transaction: flushes, never commits.

    A count recorded without a pack ("1 each" of an ingredient counted in pieces) needed
    no bridge then, and may cross one now, so counts are recomputed too."""
    counts = select(UnitRow.code).where(UnitRow.dimension == "count")
    where = PriceObservation.product_id.in_(observed_as([product_id])) & (
        PriceNorm.observation_id.is_(None)
        | (PriceNorm.status != "ok")
        | (PriceNorm.bridge_kind != "none")
        | PriceObservation.unit.in_(counts)
    )
    n = await normalize_core(db, await _dependents(db, where))
    await recipe_cost_triggers.after_product_bridge_changed(db, product_id)
    return n


# --- observations -----------------------------------------------------------


def _observation_query():
    return select(PriceObservation).options(
        joinedload(PriceObservation.product).joinedload(Product.ingredient),
        joinedload(PriceObservation.vendor_location),
        joinedload(PriceObservation.norm),
        joinedload(PriceObservation.void),
    )


async def get_observation(db: AsyncSession, observation_id: uuid.UUID) -> PriceObservation:
    row = (
        (
            await db.execute(
                _observation_query()
                .where(PriceObservation.id == observation_id)
                .execution_options(populate_existing=True)
            )
        )
        .unique()
        .scalar_one_or_none()
    )
    if row is None:
        raise ApiError(404, "not_found", "No such price observation.")
    return row


async def observe(
    db: AsyncSession,
    *,
    product_id: uuid.UUID,
    vendor_location_id: uuid.UUID,
    price: Decimal,
    qty: Decimal,
    unit: str,
    source: str,
    entered_by: AppUser | uuid.UUID,
    is_promo: bool = False,
    observed_at: datetime | None = None,
    purchase_line_id: uuid.UUID | None = None,
    listing_id: uuid.UUID | None = None,
) -> PriceObservation:
    """Insert an observation and its normalization. Flushes; the caller commits,
    so a recommit's voids and replacements land together or not at all.

    A posted price (``source = listing``) names the listing it came from; the
    default price-book views leave it out (2L).
    """
    user_id = entered_by.id if isinstance(entered_by, AppUser) else entered_by
    observation = PriceObservation(
        product_id=product_id,
        vendor_location_id=vendor_location_id,
        purchase_line_id=purchase_line_id,
        observed_at=observed_at or datetime.now(UTC),
        price=price,
        qty=qty,
        unit=unit,
        is_promo=is_promo,
        source=source,
        listing_id=listing_id,
        entered_by=user_id,
    )
    db.add(observation)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        message = str(exc.orig)
        if "already has a live price observation" in message:
            raise ApiError(
                409, "line_already_observed", "That purchase line already has a live observation."
            ) from exc
        if "unit" in message and "foreign key" in message:
            raise ApiError(422, "unknown_unit", "unit is not a known unit code.") from exc
        raise ApiError(409, "conflict", "The observation could not be saved.") from exc
    db.add(await _norm_row(db, observation))
    await db.flush()
    if purchase_line_id is None:
        # A shelf, manual or posted price qualifies on its own, so the recipes it
        # may cost are recomputed now. A purchase line's price qualifies with its
        # purchase, whose commit, recommit or reopen fires the trigger once for
        # every line (``recipe_cost_triggers.after_purchase_changed``).
        await recipe_cost_triggers.after_prices_changed(db, [observation.id])
    return await get_observation(db, observation.id)


async def void(
    db: AsyncSession, observation_id: uuid.UUID, reason: str, voided_by: AppUser | uuid.UUID
) -> PriceObservation:
    observation = await get_observation(db, observation_id)
    if observation.void is not None:
        raise ApiError(409, "already_voided", "That observation is already voided.")
    user_id = voided_by.id if isinstance(voided_by, AppUser) else voided_by
    db.add(PriceObservationVoid(observation_id=observation.id, reason=reason, voided_by=user_id))
    # Flushed, not committed: a recommit voids and then re-emits, and a failure
    # in between must roll the void back with it. The caller commits.
    await db.flush()
    await recipe_cost_triggers.after_prices_changed(db, [observation.id])
    return await get_observation(db, observation_id)


async def list_observations(
    db: AsyncSession,
    *,
    product_id: uuid.UUID | None = None,
    vendor_location_id: uuid.UUID | None = None,
    include_voided: bool = False,
    limit: int = 50,
    cursor: str | None = None,
) -> tuple[list[PriceObservation], str | None]:
    stmt = _observation_query().order_by(PriceObservation.id.desc()).limit(limit + 1)
    if product_id is not None:
        stmt = stmt.where(PriceObservation.product_id.in_(observed_as([product_id])))
    if vendor_location_id is not None:
        stmt = stmt.where(PriceObservation.vendor_location_id == vendor_location_id)
    if not include_voided:
        stmt = stmt.where(
            ~select(PriceObservationVoid.id)
            .where(PriceObservationVoid.observation_id == PriceObservation.id)
            .exists()
        )
    before = decode_cursor(cursor)
    if before is not None:
        stmt = stmt.where(PriceObservation.id < before)
    rows = list((await db.execute(stmt)).unique().scalars())
    next_cursor = encode_cursor(rows[limit - 1].id) if len(rows) > limit else None
    return rows[:limit], next_cursor


async def needs_bridge(db: AsyncSession) -> list[dict]:
    """Current observations whose normalization failed, grouped by product."""
    rows = await db.execute(
        text(
            """
            SELECT p.id AS product_id, p.name, p.brand, p.pack_qty, p.pack_unit,
                   i.id AS ingredient_id, i.name AS ingredient_name, i.canonical_unit,
                   i.category, pc.norm_status AS status, count(*) AS observation_count,
                   min(pc.observed_at) AS first_observed_at,
                   max(pc.observed_at) AS latest_observed_at
            FROM price_current pc
            JOIN product p ON p.id = pc.product_id
            JOIN ingredient i ON i.id = p.ingredient_id
            WHERE pc.norm_status IS NOT NULL AND pc.norm_status <> 'ok'
            GROUP BY p.id, p.name, p.brand, p.pack_qty, p.pack_unit, i.id, i.name,
                     i.canonical_unit, i.category, pc.norm_status
            ORDER BY latest_observed_at DESC
            """
        )
    )
    return [dict(r) for r in rows.mappings()]


async def count_norm_rows(db: AsyncSession) -> int:
    return int((await db.execute(select(func.count()).select_from(PriceNorm))).scalar_one())
