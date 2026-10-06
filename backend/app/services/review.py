"""Editing a receipt purchase in review: header, lines, attachments (Phase 2D)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import case, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.ingest.lines import PRICE_FLAGS, QTY_FLAGS, prints_an_amount, quantity_only
from app.ingest.lines import restored as restored_amount
from app.models import AppUser, Product, PurchaseLine
from app.models.geo import VendorLocation
from app.models.units import UnitRow
from app.schemas.purchases import (
    LineAdd,
    LineEdit,
    LineMerge,
    LineRow,
    LinesReplace,
    PurchaseHeaderEdit,
)
from app.services.normalize import normalize_receipt_text
from app.services.purchases import (
    TOTAL_TOLERANCE,
    ensure_not_voided,
    get_purchase,
    lines_total,
    next_seq,
    remove_line,
)
from app.services.resolution import resolve_line, upsert_alias

_FOUR = Decimal("0.0001")


def _editable(purchase) -> None:
    ensure_not_voided(purchase)
    if purchase.status == "committed":
        raise ApiError(409, "committed", "Reopen the purchase before editing it.")


async def edit_header(db: AsyncSession, purchase_id: uuid.UUID, payload: PurchaseHeaderEdit):
    purchase = await get_purchase(db, purchase_id, lock=True)
    _editable(purchase)
    data = payload.model_dump(exclude_unset=True)
    if data.pop("clear_ledger_txn_ref", False):
        purchase.ledger_txn_ref = None
    location_id = data.get("vendor_location_id")
    if location_id is not None and await db.get(VendorLocation, location_id) is None:
        raise ApiError(404, "not_found", "No such vendor location.")
    for key, value in data.items():
        if value is not None:
            setattr(purchase, key, value)
    # A value the reader could not find, now given by a person, is no longer
    # missing; the flag would otherwise follow the purchase past commit.
    answered = {
        flag
        for field, flag in (
            ("purchased_at", "purchased_at_missing"),
            ("total", "total_missing"),
            ("total", "total_not_in_scan"),
        )
        if data.get(field) is not None
    }
    if answered:
        purchase.flags = [f for f in purchase.flags if f not in answered]
    if data.get("total") is not None or data.get("tax") is not None:
        purchase.flags = _rechecked_total(purchase)
    await db.commit()
    return await get_purchase(db, purchase_id)


def _rechecked_total(purchase) -> list[str]:
    """The flags with the total checked again against the lines.

    The reader compares the printed total with the lines only when it could read
    one. A total typed in review is the first chance to catch a misread line on
    such a receipt, so it is checked the same way (header tax counts only when no
    line carries tax, as at ingest).
    """
    if "total_missing" in purchase.flags or purchase.total is None:
        # The total is the line sum standing in for one nobody has read yet:
        # there is nothing to check the lines against, only themselves.
        return list(purchase.flags)
    flags = [
        f
        for f in purchase.flags
        if f not in ("reconcile_mismatch", "total_mismatch", "decimals_restore_total")
    ]
    expected = lines_total(purchase)
    if abs(expected - purchase.total) > TOTAL_TOLERANCE:
        flags.append("total_mismatch")
        # The hint that restoring the lost decimal points reconciles is only as
        # good as the total it was checked against: check it again (#59).
        restored = expected - sum(
            line.line_total - restored_amount(line.line_total)
            for line in purchase.lines
            if "decimal_missing" in line.flags
        )
        if restored != expected and abs(restored - purchase.total) <= TOTAL_TOLERANCE:
            flags.append("decimals_restore_total")
    else:
        # The lines match the printed total, so none can still be missing: the
        # warning that part of the receipt was unread has been answered (#60).
        flags = [f for f in flags if f != "lines_partial"]
    return flags


def _parent_ok(purchase, line: PurchaseLine, parent_id: uuid.UUID | None) -> None:
    if parent_id is None:
        return
    if parent_id == line.id:
        raise ApiError(422, "self_parent", "A line cannot attach to itself.")
    parent = next((x for x in purchase.lines if x.id == parent_id), None)
    if parent is None:
        raise ApiError(404, "not_found", "No such parent line on this purchase.")
    if parent.line_kind != "item":
        raise ApiError(422, "parent_not_item", "Discounts and deposits attach to item lines.")
    if line.line_kind not in ("discount", "deposit"):
        raise ApiError(422, "not_attachable", "Only discounts and deposits attach to an item.")


async def add_line(db: AsyncSession, purchase_id: uuid.UUID, payload: LineAdd):
    purchase = await get_purchase(db, purchase_id, lock=True)
    _editable(purchase)
    # Numbers run over removed lines too, so a number always names one line (#72).
    last = await next_seq(db, purchase_id) - 1
    if payload.after_seq is None or payload.after_seq >= last:
        seq = last + 1
    else:
        seq = payload.after_seq + 1
        # Make room in two steps: the (purchase, seq) uniqueness is checked row
        # by row, so shifting in place could collide with the next line.
        await _shift_seqs(db, purchase_id, seq)
        purchase = await get_purchase(db, purchase_id)
    if payload.product_id is not None and await db.get(Product, payload.product_id) is None:
        raise ApiError(404, "not_found", "No such product.")
    line = PurchaseLine(
        seq=seq,
        raw_text=payload.raw_text,
        raw_text_norm=normalize_receipt_text(payload.raw_text or ""),
        line_kind=payload.line_kind,
        product_id=payload.product_id if payload.line_kind == "item" else None,
        qty=payload.qty,
        unit=payload.unit,
        unit_price=payload.unit_price,
        line_total=payload.line_total.quantize(_FOUR),
        resolution="manual" if payload.product_id is not None else "unmatched",
        flags=[],
        suggestions=[],
    )
    purchase.lines.append(line)
    await db.flush()
    _parent_ok(purchase, line, payload.parent_line_id)
    line.parent_line_id = payload.parent_line_id
    # A corrected line can settle (or raise) a mismatch with the printed total.
    purchase.flags = _rechecked_total(purchase)
    await db.commit()
    return await get_purchase(db, purchase_id)


async def _shift_seqs(db: AsyncSession, purchase_id: uuid.UUID, from_seq: int) -> None:
    """Move every line numbered from_seq or later, removed ones included, up by one."""
    at_or_after = (PurchaseLine.purchase_id == purchase_id) & (PurchaseLine.seq >= from_seq)
    # The caller reloads the purchase afterwards, so the session needn't track it.
    options = {"synchronize_session": False}
    await db.execute(
        update(PurchaseLine).where(at_or_after).values(seq=-PurchaseLine.seq - 1),
        execution_options=options,
    )
    await db.execute(
        update(PurchaseLine)
        .where(PurchaseLine.purchase_id == purchase_id, PurchaseLine.seq < 0)
        .values(seq=-PurchaseLine.seq),
        execution_options=options,
    )


async def edit_line(
    db: AsyncSession, purchase_id: uuid.UUID, line_id: uuid.UUID, payload: LineEdit
):
    purchase = await get_purchase(db, purchase_id, lock=True)
    _editable(purchase)
    line = next((x for x in purchase.lines if x.id == line_id), None)
    if line is None:
        raise ApiError(404, "not_found", "No such line on this purchase.")
    data = payload.model_dump(exclude_unset=True)
    # A person has now said what the quantity is: it is no longer inferred,
    # corrected from the print, or assumed (#31). A price alone says nothing about
    # the quantity, so it leaves the warning in place.
    if {"qty", "unit", "clear_qty"} & data.keys():
        line.flags = [f for f in line.flags if f not in QTY_FLAGS]
    # A person has now given the price: it is no longer a suspected misreading (#59).
    if data.get("line_total") is not None:
        line.flags = [f for f in line.flags if f not in PRICE_FLAGS]
    if data.pop("clear_parent", False):
        line.parent_line_id = None
    if data.pop("clear_qty", False):
        line.qty = None
        line.unit = None
        line.unit_price = None
    if "parent_line_id" in data and data["parent_line_id"] is not None:
        _parent_ok(purchase, line, data["parent_line_id"])
        line.parent_line_id = data.pop("parent_line_id")
    data.pop("parent_line_id", None)
    if "raw_text" in data:
        line.raw_text = data.pop("raw_text")
        line.raw_text_norm = normalize_receipt_text(line.raw_text or "")
    if data.get("line_kind") is not None and data["line_kind"] != "item":
        line.product_id = None
        line.resolution = "unmatched"
    for key, value in data.items():
        if value is not None:
            setattr(line, key, value if key != "line_total" else value.quantize(_FOUR))
    # A corrected line can settle (or raise) a mismatch with the printed total.
    purchase.flags = _rechecked_total(purchase)
    await db.commit()
    return await get_purchase(db, purchase_id)


async def delete_line(db: AsyncSession, user: AppUser, purchase_id: uuid.UUID, line_id: uuid.UUID):
    purchase = await get_purchase(db, purchase_id, lock=True)
    _editable(purchase)
    line = next((x for x in purchase.lines if x.id == line_id), None)
    if line is None:
        raise ApiError(404, "not_found", "No such line on this purchase.")
    # A line that reached the price book is voided and kept as its price's
    # provenance; one that never did is deleted (#72).
    await remove_line(db, user, purchase, line)
    # A corrected line can settle (or raise) a mismatch with the printed total.
    purchase.flags = _rechecked_total(purchase)
    await db.commit()
    return await get_purchase(db, purchase_id)


async def merge_line(
    db: AsyncSession, user: AppUser, purchase_id: uuid.UUID, line_id: uuid.UUID, payload: LineMerge
):
    """Join a line that is only a weight or count to the item it belongs to (#87).

    The item takes the printed quantity and rate. It keeps its own amount unless
    it prints none (a name row, "BANANAS", with the price on the weight row
    beneath it), when it takes the weight row's. The weight row is then taken
    off the purchase as Delete would take it, and whatever was attached to it
    moves to the item.
    """
    purchase = await get_purchase(db, purchase_id, lock=True)
    _editable(purchase)
    line = next((x for x in purchase.lines if x.id == line_id), None)
    target = next((x for x in purchase.lines if x.id == payload.into_line_id), None)
    if line is None or target is None:
        raise ApiError(404, "not_found", "No such line on this purchase.")
    if target is line:
        raise ApiError(422, "self_merge", "A line cannot merge into itself.")
    if target.line_kind != "item":
        raise ApiError(422, "not_an_item", "A weight or count merges into an item line.")
    printed = quantity_only(line.raw_text or "")
    if printed is None:
        raise ApiError(
            422, "not_a_quantity", "Only a line that is just a weight or count can be merged."
        )
    target.qty, target.unit, target.unit_price = printed.qty, printed.unit, printed.rate
    if printed.amount is not None and not prints_an_amount(target.raw_text or ""):
        target.line_total = printed.amount.quantize(_FOUR)
    # A person has now said where the quantity belongs.
    target.flags = [f for f in target.flags if f not in QTY_FLAGS]
    for other in purchase.lines:
        if other.parent_line_id == line.id:
            other.parent_line_id = target.id
    await remove_line(db, user, purchase, line)
    purchase.flags = _rechecked_total(purchase)
    await db.commit()
    return await get_purchase(db, purchase_id)


def _text(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _same_money(a: Decimal | None, b: Decimal | None) -> bool:
    if a is None or b is None:
        return a is b
    return a.quantize(_FOUR) == b.quantize(_FOUR)


def _check_rows(purchase, rows: list[LineRow]) -> None:
    saved = {line.id for line in purchase.lines}
    given = [row.id for row in rows if row.id is not None]
    unknown = [str(i) for i in given if i not in saved]
    if unknown:
        raise ApiError(422, "unknown_line", "No such line on this purchase.", {"line_ids": unknown})
    if len(set(given)) != len(given):
        raise ApiError(422, "validation_error", "A line appears twice.")
    for at, row in enumerate(rows):
        if row.attach_to is None:
            continue
        if row.attach_to >= len(rows) or row.attach_to == at:
            raise ApiError(422, "bad_attach", f"Row {at + 1} attaches to no other row.")
        if row.line_kind not in ("discount", "deposit"):
            raise ApiError(422, "not_attachable", "Only discounts and deposits attach to an item.")
        if rows[row.attach_to].line_kind != "item":
            raise ApiError(422, "parent_not_item", "Discounts and deposits attach to item lines.")


async def _check_units(db: AsyncSession, rows: list[LineRow]) -> None:
    wanted = {row.unit for row in rows if row.unit}
    if not wanted:
        return
    known = set((await db.execute(select(UnitRow.code).where(UnitRow.code.in_(wanted)))).scalars())
    if wanted - known:
        raise ApiError(422, "unknown_unit", f"There is no unit {sorted(wanted - known)[0]!r}.")


def _apply_row(line: PurchaseLine, row: LineRow) -> tuple[bool, bool]:
    """Write a row onto its saved line; return (text changed, now needs resolving).

    Flags are cleared as the single-line edit clears them: a quantity a person
    gives is no longer inferred, and a price a person gives is no longer a
    suspected misreading. Unchanged values clear nothing.
    """
    qty, unit = row.qty, (row.unit or None) if row.qty is not None else None
    total = row.line_total.quantize(_FOUR)
    text = _text(row.raw_text)
    qty_changed = qty != line.qty or unit != line.unit
    total_changed = not _same_money(total, line.line_total)
    text_changed = text != _text(line.raw_text)
    became_item = row.line_kind == "item" and line.line_kind != "item"
    if qty_changed:
        line.flags = [f for f in line.flags if f not in QTY_FLAGS]
    if total_changed:
        line.flags = [f for f in line.flags if f not in PRICE_FLAGS]
    if qty_changed or total_changed:
        # A printed rate stays while it still makes the line's amount.
        keeps_rate = (
            line.unit_price is not None
            and qty is not None
            and abs(line.unit_price * qty - total) <= TOTAL_TOLERANCE
        )
        if not keeps_rate:
            line.unit_price = None
    line.qty, line.unit, line.line_total = qty, unit, total
    if text_changed:
        line.raw_text = text
        line.raw_text_norm = normalize_receipt_text(text or "")
    if row.line_kind != line.line_kind:
        line.line_kind = row.line_kind
        if row.line_kind != "item":
            line.product_id = None
            line.resolution = "unmatched"
            line.resolved_by = None
            line.suggestions = []
    return text_changed, became_item or (text_changed and line.line_kind == "item")


async def replace_lines(
    db: AsyncSession, user: AppUser, purchase_id: uuid.UUID, payload: LinesReplace
):
    """Save "Correct the lines" (#182): every line of a draft, in one transaction.

    Rows are matched to saved lines by id, never by position, so a line nobody
    changed keeps its product and flags. A saved line left out is removed as
    Delete removes it. New rows, and rows whose wording or kind changed, are
    resolved again, except where a person already chose the product: that choice
    stands, and the corrected wording is what its alias learns.
    """
    purchase = await get_purchase(db, purchase_id, lock=True)
    _editable(purchase)
    rows = payload.lines
    _check_rows(purchase, rows)
    await _check_units(db, rows)

    kept = {row.id for row in rows if row.id is not None}
    for line in [x for x in purchase.lines if x.id not in kept]:
        await remove_line(db, user, purchase, line)

    saved = {line.id: line for line in purchase.lines}
    vendor_id = purchase.vendor_location.vendor_id if purchase.vendor_location else None
    seq = await next_seq(db, purchase_id)
    ordered: list[PurchaseLine] = []
    to_resolve: list[PurchaseLine] = []
    for row in rows:
        if row.id is None:
            text = _text(row.raw_text)
            line = PurchaseLine(
                # A number past every line's, removed ones included; renumbered below.
                seq=seq,
                raw_text=text,
                raw_text_norm=normalize_receipt_text(text or ""),
                line_kind=row.line_kind,
                qty=row.qty,
                unit=(row.unit or None) if row.qty is not None else None,
                line_total=row.line_total.quantize(_FOUR),
                resolution="unmatched",
                flags=[],
                suggestions=[],
            )
            seq += 1
            purchase.lines.append(line)
            if line.line_kind == "item" and text:
                to_resolve.append(line)
        else:
            line = saved[row.id]
            text_changed, needs_resolving = _apply_row(line, row)
            if line.resolved_by is not None and line.line_kind == "item":
                # A person chose this line's product (or to ignore it): keep the
                # choice, and learn it under the wording as now corrected.
                if text_changed and vendor_id is not None and line.raw_text_norm:
                    await upsert_alias(db, vendor_id, line.raw_text_norm, line.product_id)
            elif needs_resolving:
                to_resolve.append(line)
        ordered.append(line)
    await db.flush()

    for at, row in enumerate(rows):
        line = ordered[at]
        if row.attach_to is not None:
            line.parent_line_id = ordered[row.attach_to].id
        elif line.parent_line_id is not None and (
            line.line_kind not in ("discount", "deposit")
            or line.parent_line_id not in {x.id for x in ordered if x.line_kind == "item"}
        ):
            line.parent_line_id = None
    for line in to_resolve:
        await resolve_line(db, purchase, line)
    purchase.flags = _rechecked_total(purchase)
    await db.flush()
    await _renumber(db, purchase_id, [line.id for line in ordered])
    await db.commit()
    return await get_purchase(db, purchase_id)


async def _renumber(db: AsyncSession, purchase_id: uuid.UUID, order: list[uuid.UUID]) -> None:
    """Number the lines 1.. in the order given, removed lines after them.

    Removed lines keep their numbers' order among themselves, so each number
    still names one line (#72). Two steps, as _shift_seqs does: the uniqueness of
    (purchase, seq) is checked row by row.
    """
    removed = (
        await db.execute(
            select(PurchaseLine.id)
            .where(PurchaseLine.purchase_id == purchase_id, PurchaseLine.removed_at.is_not(None))
            .order_by(PurchaseLine.seq)
        )
    ).scalars()
    target = {line_id: n for n, line_id in enumerate([*order, *removed], start=1)}
    if not target:
        return
    options = {"synchronize_session": False}
    mine = PurchaseLine.purchase_id == purchase_id
    await db.execute(
        update(PurchaseLine).where(mine).values(seq=-PurchaseLine.seq - 1),
        execution_options=options,
    )
    await db.execute(
        update(PurchaseLine).where(mine).values(seq=case(target, value=PurchaseLine.id)),
        execution_options=options,
    )


async def re_resolve_line(db: AsyncSession, purchase_id: uuid.UUID, line_id: uuid.UUID):
    purchase = await get_purchase(db, purchase_id, lock=True)
    ensure_not_voided(purchase)
    line = next((x for x in purchase.lines if x.id == line_id), None)
    if line is None:
        raise ApiError(404, "not_found", "No such line on this purchase.")
    line.resolved_by = None
    await resolve_line(db, purchase, line)
    await db.commit()
    return await get_purchase(db, purchase_id)
