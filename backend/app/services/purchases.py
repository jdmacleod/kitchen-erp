"""Manual purchases (2B) and the shared purchase read model used by review (2D)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy.orm.attributes import set_committed_value

from app.core.errors import ApiError
from app.models import (
    AppUser,
    PriceObservation,
    PriceObservationVoid,
    Product,
    Purchase,
    PurchaseLine,
)
from app.models.geo import VendorLocation
from app.schemas.purchases import LineIn, ManualPurchaseIn
from app.services import pricebook
from app.services.pagination import decode_cursor, encode_cursor

_CTX = Context(prec=28, rounding=ROUND_HALF_EVEN)
_FOUR = Decimal("0.0001")
TOTAL_TOLERANCE = Decimal("0.02")


def complete_line(line: LineIn) -> tuple[Decimal, Decimal]:
    """Return (unit_price, line_total), computing whichever was not given."""
    if line.unit_price is not None:
        total = _CTX.multiply(line.unit_price, line.qty).quantize(_FOUR, rounding=ROUND_HALF_EVEN)
        return line.unit_price.quantize(_FOUR, rounding=ROUND_HALF_EVEN), total
    assert line.line_total is not None
    price = _CTX.divide(line.line_total, line.qty).quantize(_FOUR, rounding=ROUND_HALF_EVEN)
    return price, line.line_total.quantize(_FOUR, rounding=ROUND_HALF_EVEN)


def computed_total(purchase: Purchase) -> Decimal:
    """Items less discounts plus tax, deposits, and fees."""
    total = Decimal("0")
    for line in purchase.lines:
        if line.line_kind == "discount":
            total -= abs(line.line_total)
        else:
            total += line.line_total
    return total.quantize(_FOUR, rounding=ROUND_HALF_EVEN)


def _purchase_query():
    return select(Purchase).options(
        # The ingredient gives each line its category chip (UI-1.6).
        selectinload(Purchase.lines)
        .joinedload(PurchaseLine.product)
        .joinedload(Product.ingredient),
        joinedload(Purchase.vendor_location),
    )


async def get_purchase(db: AsyncSession, purchase_id: uuid.UUID) -> Purchase:
    row = (
        (
            await db.execute(
                _purchase_query()
                .where(Purchase.id == purchase_id)
                .execution_options(populate_existing=True)
            )
        )
        .unique()
        .scalar_one_or_none()
    )
    if row is None:
        raise ApiError(404, "not_found", "No such purchase.")
    return row


async def list_purchases(
    db: AsyncSession,
    *,
    vendor_location_id: uuid.UUID | None = None,
    status: str | None = None,
    source: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> tuple[list[Purchase], str | None]:
    stmt = _purchase_query().order_by(Purchase.id.desc()).limit(limit + 1)
    if vendor_location_id is not None:
        stmt = stmt.where(Purchase.vendor_location_id == vendor_location_id)
    if status is not None:
        stmt = stmt.where(Purchase.status == status)
    if source is not None:
        stmt = stmt.where(Purchase.source == source)
    before = decode_cursor(cursor)
    if before is not None:
        stmt = stmt.where(Purchase.id < before)
    rows = list((await db.execute(stmt)).unique().scalars())
    next_cursor = encode_cursor(rows[limit - 1].id) if len(rows) > limit else None
    return rows[:limit], next_cursor


async def live_observations(db: AsyncSession, purchase: Purchase) -> dict[uuid.UUID, uuid.UUID]:
    """Map purchase_line_id -> live (non-voided) observation id."""
    line_ids = [line.id for line in purchase.lines]
    if not line_ids:
        return {}
    stmt = select(PriceObservation.purchase_line_id, PriceObservation.id).where(
        PriceObservation.purchase_line_id.in_(line_ids),
        ~select(PriceObservationVoid.id)
        .where(PriceObservationVoid.observation_id == PriceObservation.id)
        .exists(),
    )
    return dict((await db.execute(stmt)).all())


async def recorded_lines(db: AsyncSession, lines: Iterable[PurchaseLine]) -> set[uuid.UUID]:
    """Lines that have ever emitted an observation, voided or not.

    Observations are append-only and keep their line as provenance, so such a
    line can be ignored but never deleted.
    """
    line_ids = [line.id for line in lines]
    if not line_ids:
        return set()
    stmt = select(PriceObservation.purchase_line_id).where(
        PriceObservation.purchase_line_id.in_(line_ids)
    )
    return set((await db.execute(stmt)).scalars())


async def next_seq(db: AsyncSession, purchase_id: uuid.UUID) -> int:
    """The number after every line's, removed ones included, so a number names one line."""
    stmt = select(func.coalesce(func.max(PurchaseLine.seq), 0)).where(
        PurchaseLine.purchase_id == purchase_id
    )
    return int((await db.execute(stmt)).scalar_one()) + 1


async def remove_line(
    db: AsyncSession, user: AppUser, purchase: Purchase, line: PurchaseLine
) -> int:
    """Take a line off its purchase; return how many prices that voided (0 or 1).

    A line that never reached the price book is deleted. One that did is kept as
    its observation's provenance: the live observation is voided and the line is
    marked removed, which hides it from every reader of ``purchase.lines`` (#72).
    Either way, discounts and deposits attached to it are detached. Flushes; the
    caller commits.
    """
    for other in purchase.lines:
        if other.parent_line_id == line.id:
            other.parent_line_id = None
    if not await recorded_lines(db, [line]):
        purchase.lines.remove(line)
        await db.flush()
        return 0
    voided = 0
    live = await live_observations(db, purchase)
    if line.id in live:
        await pricebook.void(db, live[line.id], "line removed", user)
        voided = 1
    line.removed_at = datetime.now(UTC)
    line.removed_by = user.id
    # Out of the loaded collection without counting as an orphan, which would
    # delete the row the observation points at.
    set_committed_value(purchase, "lines", [x for x in purchase.lines if x is not line])
    await db.flush()
    return voided


async def resolver_names(db: AsyncSession, purchases: Iterable[Purchase]) -> dict[uuid.UUID, str]:
    """Map user id -> display name for everyone who resolved a line of these purchases.

    One query for a whole page of purchases, none when nobody resolved anything.
    """
    ids = {line.resolved_by for p in purchases for line in p.lines if line.resolved_by is not None}
    if not ids:
        return {}
    stmt = select(AppUser.id, AppUser.display_name).where(AppUser.id.in_(ids))
    return dict((await db.execute(stmt)).all())


async def _check_location(db: AsyncSession, location_id: uuid.UUID) -> VendorLocation:
    location = await db.get(VendorLocation, location_id)
    if location is None:
        raise ApiError(404, "not_found", "No such vendor location.")
    return location


async def _check_products(db: AsyncSession, lines: list[LineIn]) -> None:
    ids = {line.product_id for line in lines}
    found = set((await db.execute(select(Product.id).where(Product.id.in_(ids)))).scalars())
    missing = ids - found
    if missing:
        raise ApiError(
            404, "not_found", "No such product.", {"product_ids": [str(m) for m in missing]}
        )


def _apply_header(purchase: Purchase, payload: ManualPurchaseIn, subtotal: Decimal) -> None:
    purchase.vendor_location_id = payload.vendor_location_id
    purchase.purchased_at = payload.purchased_at
    purchase.subtotal = subtotal
    purchase.total = payload.total if payload.total is not None else subtotal
    flags = [f for f in purchase.flags if f != "total_mismatch"]
    if payload.total is not None and abs(payload.total - subtotal) > TOTAL_TOLERANCE:
        flags.append("total_mismatch")
    purchase.flags = flags


async def _emit(db: AsyncSession, purchase: Purchase, line: PurchaseLine, user: AppUser) -> None:
    await pricebook.observe(
        db,
        product_id=line.product_id,
        vendor_location_id=purchase.vendor_location_id,
        price=line.line_total,
        qty=line.qty,
        unit=line.unit,
        source="manual",
        entered_by=user,
        observed_at=purchase.purchased_at,
        purchase_line_id=line.id,
    )


def _new_line(
    seq: int, line_in: LineIn, price: Decimal, total: Decimal, user: AppUser
) -> PurchaseLine:
    return PurchaseLine(
        seq=seq,
        line_kind="item",
        product_id=line_in.product_id,
        qty=line_in.qty,
        unit=line_in.unit,
        unit_price=price,
        line_total=total,
        resolution="manual",
        resolved_by=user.id,
        flags=[],
    )


async def create_manual(db: AsyncSession, user: AppUser, payload: ManualPurchaseIn) -> Purchase:
    """A manual purchase commits immediately and emits one observation per line."""
    await _check_location(db, payload.vendor_location_id)
    await _check_products(db, payload.lines)
    purchase = Purchase(source="manual", status="committed", entered_by=user.id, flags=[])
    subtotal = Decimal("0")
    for seq, line_in in enumerate(payload.lines, start=1):
        price, total = complete_line(line_in)
        subtotal += total
        purchase.lines.append(_new_line(seq, line_in, price, total, user))
    _apply_header(purchase, payload, subtotal)
    db.add(purchase)
    await db.flush()
    for line in purchase.lines:
        await _emit(db, purchase, line, user)
    await db.commit()
    return await get_purchase(db, purchase.id)


def _same(line: PurchaseLine, line_in: LineIn, price: Decimal, total: Decimal) -> bool:
    return (
        line.product_id == line_in.product_id
        and line.qty == line_in.qty
        and line.unit == line_in.unit
        and line.unit_price == price
        and line.line_total == total
    )


async def update_manual(
    db: AsyncSession, user: AppUser, purchase_id: uuid.UUID, payload: ManualPurchaseIn
) -> Purchase:
    """Reopen and recommit. Changed lines void their observation and emit a new
    one; untouched lines keep theirs.

    Lines are matched by id: a line with an id updates that line, one without
    is new, and a saved line missing from the body is removed (#72). Matching by
    position would overwrite every line after a removed one with its neighbour.
    """
    purchase = await get_purchase(db, purchase_id)
    if purchase.source not in ("manual", "import"):
        raise ApiError(
            409,
            "not_manual",
            "Only manual purchases are edited this way; reopen receipts in review.",
        )
    await _check_location(db, payload.vendor_location_id)
    await _check_products(db, payload.lines)
    header_changed = (
        purchase.vendor_location_id != payload.vendor_location_id
        or purchase.purchased_at != payload.purchased_at
    )
    existing = {line.id: line for line in purchase.lines}
    given = [line_in.id for line_in in payload.lines if line_in.id is not None]
    unknown = [str(i) for i in given if i not in existing]
    if unknown:
        raise ApiError(422, "unknown_line", "No such line on this purchase.", {"line_ids": unknown})
    if len(set(given)) != len(given):
        raise ApiError(422, "validation_error", "A line appears twice.")
    for line in [x for x in purchase.lines if x.id not in set(given)]:
        await remove_line(db, user, purchase, line)
    live = await live_observations(db, purchase)
    seq = await next_seq(db, purchase.id)
    subtotal = Decimal("0")
    to_emit: list[PurchaseLine] = []
    for line_in in payload.lines:
        price, total = complete_line(line_in)
        subtotal += total
        if line_in.id is None:
            line = _new_line(seq, line_in, price, total, user)
            seq += 1
            purchase.lines.append(line)
            to_emit.append(line)
            continue
        line = existing[line_in.id]
        if _same(line, line_in, price, total) and not header_changed and line.id in live:
            continue
        if line.id in live:
            await pricebook.void(db, live[line.id], "line edited on recommit", user)
        line.product_id = line_in.product_id
        line.qty, line.unit = line_in.qty, line_in.unit
        line.unit_price, line.line_total = price, total
        line.resolution, line.resolved_by = "manual", user.id
        to_emit.append(line)
    _apply_header(purchase, payload, subtotal)
    await db.flush()
    for line in to_emit:
        await _emit(db, purchase, line, user)
    await db.commit()
    return await get_purchase(db, purchase_id)


async def last_purchase_unit(db: AsyncSession, product_id: uuid.UUID) -> str | None:
    stmt = (
        select(PurchaseLine.unit)
        .where(
            PurchaseLine.product_id == product_id,
            PurchaseLine.unit.is_not(None),
            PurchaseLine.removed_at.is_(None),
        )
        .order_by(PurchaseLine.created_at.desc(), PurchaseLine.id.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()
