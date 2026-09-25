"""The price book: append-only observations, voids, and rebuildable normalization.

Rounding rule (the only one in the price book): arithmetic in a 28-digit Decimal
context; the normalized unit price is quantized to six places, ROUND_HALF_EVEN.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal

from sqlalchemy import delete, func, select, text
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
from app.services.pagination import decode_cursor, encode_cursor
from app.services.units import build_context
from app.units import CanonicalQty, ConversionFailure, convert

_CTX = Context(prec=28, rounding=ROUND_HALF_EVEN)
_SIX = Decimal("0.000001")


def unit_price(price: Decimal, canonical_qty: Decimal) -> Decimal:
    return _CTX.divide(price, canonical_qty).quantize(_SIX, rounding=ROUND_HALF_EVEN)


# --- normalization ----------------------------------------------------------


async def _norm_row(db: AsyncSession, observation: PriceObservation) -> PriceNorm:
    product = await db.get(Product, observation.product_id)
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


async def normalize(db: AsyncSession, observation_ids: list[uuid.UUID]) -> int:
    """Recompute price_norm for the given observations. Commits."""
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


async def recompute_for_ingredient(db: AsyncSession, ingredient_id: uuid.UUID) -> int:
    """After a density or named-measure change: every observation of the
    ingredient's products that crossed a bridge, failed to, or crossed a pack
    (a pack may itself have crossed the density). Same-dimension observations
    depend on nothing and are left alone."""
    product_ids = select(Product.id).where(Product.ingredient_id == ingredient_id)
    where = PriceObservation.product_id.in_(product_ids) & (
        PriceNorm.observation_id.is_(None)
        | (PriceNorm.status != "ok")
        | (PriceNorm.bridge_kind != "none")
    )
    return await normalize(db, await _dependents(db, where))


async def recompute_for_product(db: AsyncSession, product_id: uuid.UUID) -> int:
    """After a pack or density-override change on a product."""
    where = (PriceObservation.product_id == product_id) & (
        PriceNorm.observation_id.is_(None)
        | (PriceNorm.status != "ok")
        | (PriceNorm.bridge_kind != "none")
    )
    return await normalize(db, await _dependents(db, where))


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
) -> PriceObservation:
    """Insert an observation and its normalization. Commits."""
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
    await db.commit()
    return await get_observation(db, observation.id)


async def void(
    db: AsyncSession, observation_id: uuid.UUID, reason: str, voided_by: AppUser | uuid.UUID
) -> PriceObservation:
    observation = await get_observation(db, observation_id)
    if observation.void is not None:
        raise ApiError(409, "already_voided", "That observation is already voided.")
    user_id = voided_by.id if isinstance(voided_by, AppUser) else voided_by
    db.add(PriceObservationVoid(observation_id=observation.id, reason=reason, voided_by=user_id))
    await db.commit()
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
        stmt = stmt.where(PriceObservation.product_id == product_id)
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
                   pc.norm_status AS status, count(*) AS observation_count,
                   min(pc.observed_at) AS first_observed_at,
                   max(pc.observed_at) AS latest_observed_at
            FROM price_current pc
            JOIN product p ON p.id = pc.product_id
            JOIN ingredient i ON i.id = p.ingredient_id
            WHERE pc.norm_status IS NOT NULL AND pc.norm_status <> 'ok'
            GROUP BY p.id, p.name, p.brand, p.pack_qty, p.pack_unit, i.id, i.name,
                     i.canonical_unit, pc.norm_status
            ORDER BY latest_observed_at DESC
            """
        )
    )
    return [dict(r) for r in rows.mappings()]


async def count_norm_rows(db: AsyncSession) -> int:
    return int((await db.execute(select(func.count()).select_from(PriceNorm))).scalar_one())
