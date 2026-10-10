"""Manual purchases (2B) and the shared purchase read model used by review (2D)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal
from typing import Literal

from sqlalchemy import and_, exists, func, literal, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, joinedload, selectinload
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
from app.models.geo import Vendor, VendorLocation
from app.schemas.purchases import LineIn, ManualPurchaseIn
from app.services import best_by, pricebook
from app.services.pagination import decode_keyset, encode_keyset

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


def lines_total(purchase: Purchase) -> Decimal:
    """What the lines say the receipt came to, to set beside its printed total.

    The header's tax counts only when no line carries tax, as at ingest.
    """
    header_tax = purchase.tax is not None and not any(
        line.line_kind == "tax" for line in purchase.lines
    )
    return computed_total(purchase) + (purchase.tax if header_tax and purchase.tax else 0)


# A draft whose lines miss its printed total by more than this share of it is held
# for a careful look (#121, ruling R2): at that size the gap is misread lines, not
# a missed coupon.
HOLD_GAP_SHARE = Decimal("0.25")

Trust = Literal["adds_up", "check_lines", "couldnt_read"]


@dataclass(frozen=True)
class ReadingCheck:
    """How far a receipt's reading can be trusted, derived from what is stored now.

    ``trust`` is None for a purchase entered by hand: there was no reading.
    ``held`` marks a draft that needs a careful look before it is committed. It
    never blocks a commit (non-negotiable 8 is about not committing *without* a
    person, and opening the review is that person).
    """

    trust: Trust | None
    held: bool
    lines_total: Decimal


def assess(
    *,
    source: str,
    status: str,
    flags: Iterable[str],
    lines_sum: Decimal,
    total: Decimal | None,
    item_lines: int,
    unscanned: int,
) -> ReadingCheck:
    """The rule behind :func:`reading_check`, on plain values so SQL can feed it too.

    ``unscanned`` counts lines flagged ``not_in_scan``. On its own it holds
    nothing: OCR drops prices often enough that a receipt which adds up would be
    held for a scan's fault. Beside a mismatch it does, because then the lines
    the scan cannot vouch for are the likely reason.
    """
    flags = set(flags)
    if source != "receipt":
        return ReadingCheck(trust=None, held=False, lines_total=lines_sum)
    if item_lines == 0:
        return ReadingCheck(trust="couldnt_read", held=False, lines_total=lines_sum)
    printed = None if "total_missing" in flags else total
    if printed is not None and abs(lines_sum - printed) <= TOTAL_TOLERANCE:
        return ReadingCheck(trust="adds_up", held=False, lines_total=lines_sum)
    held = False
    if status == "draft" and printed is not None:
        unscanned += "total_not_in_scan" in flags
        gap = abs(lines_sum - printed)
        held = printed <= 0 or gap > printed * HOLD_GAP_SHARE or unscanned > 0
    return ReadingCheck(trust="check_lines", held=held, lines_total=lines_sum)


def reading_gap(check: ReadingCheck, total: Decimal | None) -> Decimal | None:
    """How far the lines are from the printed total, when that is why they need checking."""
    if check.trust != "check_lines" or total is None:
        return None
    return abs(check.lines_total - total)


def reading_check(purchase: Purchase) -> ReadingCheck:
    return assess(
        source=purchase.source,
        status=purchase.status,
        flags=purchase.flags,
        lines_sum=lines_total(purchase),
        total=purchase.total,
        item_lines=sum(line.line_kind == "item" for line in purchase.lines),
        unscanned=sum("not_in_scan" in line.flags for line in purchase.lines),
    )


def _purchase_query():
    return select(Purchase).options(
        # The ingredient gives each line its category chip (UI-1.6).
        selectinload(Purchase.lines)
        .joinedload(PurchaseLine.product)
        .joinedload(Product.ingredient),
        joinedload(Purchase.vendor_location),
    )


async def get_purchase(db: AsyncSession, purchase_id: uuid.UUID, *, lock: bool = False) -> Purchase:
    """The purchase with its lines; ``lock`` holds its row until the transaction ends.

    Every change to a purchase locks it first, so a removal and an edit that
    would record a price cannot interleave: whichever comes second waits, then
    sees what the first did (a voided purchase, or the new price to void).
    """
    stmt = _purchase_query().where(Purchase.id == purchase_id)
    if lock:
        stmt = stmt.with_for_update(of=Purchase)
    row = (
        (await db.execute(stmt.execution_options(populate_existing=True)))
        .unique()
        .scalar_one_or_none()
    )
    if row is None:
        raise ApiError(404, "not_found", "No such purchase.")
    return row


def ensure_not_voided(purchase: Purchase) -> None:
    """A removed purchase is kept only as the record behind its voided prices."""
    if purchase.status == "voided":
        raise ApiError(409, "voided", "This purchase was removed, so it can't be changed.")


PurchaseSort = Literal["date", "where", "total"]
SortDir = Literal["asc", "desc"]

# Accented letters folded to plain ones, so "jalapeno" finds "Jalapeño" (spec 10).
_ACCENTED = "áàâäãåāéèêëēíìîïīóòôöõøōúùûüūñçýÿ"
_PLAIN = "aaaaaaaeeeeeiiiiiooooooouuuuuncyy"


def _fold(expr):
    return func.translate(func.lower(expr), _ACCENTED, _PLAIN)


def _word_start(word: str) -> str:
    """A pattern for ``word`` at a word start: first, or after a non-alphanumeric."""
    literal_word = "".join(c if c.isalnum() else "\\" + c for c in word)
    return "(^|[^a-z0-9])" + literal_word


def _fold_text(text: str) -> str:
    return text.lower().translate(str.maketrans(_ACCENTED, _PLAIN))


async def list_purchases(
    db: AsyncSession,
    *,
    vendor_location_id: uuid.UUID | None = None,
    status: str | None = None,
    source: str | None = None,
    q: str | None = None,
    sort: PurchaseSort = "date",
    direction: SortDir = "desc",
    limit: int = 50,
    cursor: str | None = None,
) -> tuple[list[Purchase], str | None]:
    """Purchases, newest bought first unless sorted otherwise (spec 10, Shop: purchases).

    ``q`` keeps purchases where every word starts a word in the store's or
    location's name, or on one line, in its receipt text or product name. Sorting by store
    or total falls back to newest first for ties. The cursor carries the sort
    value, the purchase date and the id, so the order holds across pages.
    """
    location = aliased(VendorLocation)
    vendor = aliased(Vendor)
    stmt = _purchase_query().outerjoin(location, location.id == Purchase.vendor_location_id)
    stmt = stmt.outerjoin(vendor, vendor.id == location.vendor_id)
    if vendor_location_id is not None:
        stmt = stmt.where(Purchase.vendor_location_id == vendor_location_id)
    if status is not None:
        stmt = stmt.where(Purchase.status == status)
    else:
        # Removed purchases are shown only when asked for (#74).
        stmt = stmt.where(Purchase.status != "voided")
    if source is not None:
        stmt = stmt.where(Purchase.source == source)
    words = _fold_text(q or "").split()
    if words:
        # A word matches at the start of a word ("sage" never finds "sausage"),
        # and the words a store doesn't hold must all be on one line (spec 10).
        store = _fold(func.coalesce(vendor.name, "") + " " + func.coalesce(location.name, ""))
        line_product = aliased(Product)
        line = _fold(
            func.coalesce(PurchaseLine.raw_text, "") + " " + func.coalesce(line_product.name, "")
        )
        starts = [_word_start(w) for w in words]
        on_one_line = exists(
            select(literal(1))
            .select_from(PurchaseLine)
            .outerjoin(line_product, line_product.id == PurchaseLine.product_id)
            .where(
                PurchaseLine.purchase_id == Purchase.id,
                PurchaseLine.removed_at.is_(None),
                *(or_(store.regexp_match(r), line.regexp_match(r)) for r in starts),
            )
        )
        stmt = stmt.where(or_(and_(*(store.regexp_match(r) for r in starts)), on_one_line))

    key = None
    if sort == "where":
        key = _fold(func.coalesce(vendor.name, "") + " " + func.coalesce(location.name, ""))
    elif sort == "total":
        key = Purchase.total
    newest = (Purchase.purchased_at.desc(), Purchase.id.desc())
    if key is None:
        if direction == "asc":
            stmt = stmt.order_by(Purchase.purchased_at.asc(), Purchase.id.asc())
        else:
            stmt = stmt.order_by(*newest)
    else:
        stmt = stmt.order_by(key.asc() if direction == "asc" else key.desc(), *newest)

    after = _decode_list_cursor(cursor, sort)
    if after is not None:
        value, when, last_id = after
        by_date = tuple_(Purchase.purchased_at, Purchase.id)
        if key is None:
            stmt = stmt.where(
                by_date > (when, last_id) if direction == "asc" else by_date < (when, last_id)
            )
        else:
            past = key > value if direction == "asc" else key < value
            stmt = stmt.where(or_(past, and_(key == value, by_date < (when, last_id))))
    rows = list((await db.execute(stmt.limit(limit + 1))).unique().scalars())
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        value = None
        if sort == "where":
            loc = last.vendor_location
            value = _fold_text(f"{loc.vendor.name if loc else ''} {loc.name if loc else ''}")
        elif sort == "total":
            value = str(last.total)
        next_cursor = encode_keyset(
            json.dumps([sort, value, last.purchased_at.isoformat()]), last.id
        )
    return rows[:limit], next_cursor


def _decode_list_cursor(cursor: str | None, sort: str):
    """(sort value, purchase date, id) from a list cursor made for the same sort."""
    found = decode_keyset(cursor)
    if found is None:
        return None
    raw, last_id = found
    try:
        made_for, value, when = json.loads(raw)
        if made_for != sort:
            raise ValueError("another sort")
        when = datetime.fromisoformat(when)
        if sort == "total":
            value = Decimal(value)
        elif sort == "where" and not isinstance(value, str):
            raise ValueError("where value")
    except (ValueError, TypeError, ArithmeticError) as exc:
        raise ApiError(400, "bad_cursor", "The cursor is not valid.") from exc
    return value, when, last_id


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


async def removed_line_count(db: AsyncSession, purchase_id: uuid.UUID) -> int:
    stmt = select(func.count()).where(
        PurchaseLine.purchase_id == purchase_id, PurchaseLine.removed_at.is_not(None)
    )
    return int((await db.execute(stmt)).scalar_one())


async def resolver_names(db: AsyncSession, purchases: Iterable[Purchase]) -> dict[uuid.UUID, str]:
    """Map user id -> display name for everyone who resolved a line of these
    purchases, or removed one.

    One query for a whole page of purchases, none when nobody resolved anything.
    """
    ids = {line.resolved_by for p in purchases for line in p.lines if line.resolved_by is not None}
    ids |= {p.voided_by for p in purchases if p.voided_by is not None}
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
    await best_by.refresh(db, purchase)
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
    purchase = await get_purchase(db, purchase_id, lock=True)
    ensure_not_voided(purchase)
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
    if existing and not given:
        # A client that still matches by position would otherwise remove every
        # saved line and re-add it, voiding prices nobody changed.
        raise ApiError(
            422,
            "line_ids_required",
            "Name each saved line by its id; a saved line left out is removed.",
        )
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
    repointed: list[uuid.UUID] = []
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
        if line.product_id != line_in.product_id:
            repointed.append(line.id)
        line.product_id = line_in.product_id
        line.qty, line.unit = line_in.qty, line_in.unit
        line.unit_price, line.line_total = price, total
        line.resolution, line.resolved_by = "manual", user.id
        to_emit.append(line)
    _apply_header(purchase, payload, subtotal)
    await db.flush()
    for line in to_emit:
        await _emit(db, purchase, line, user)
    await best_by.refresh(db, purchase, repointed=repointed)
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
