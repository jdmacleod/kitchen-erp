"""Editing a receipt purchase in review: header, lines, attachments (Phase 2D)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.ingest.lines import PRICE_FLAGS, QTY_FLAGS
from app.ingest.lines import restored as restored_amount
from app.models import AppUser, Product, PurchaseLine
from app.models.geo import VendorLocation
from app.schemas.purchases import LineAdd, LineEdit, PurchaseHeaderEdit
from app.services.normalize import normalize_receipt_text
from app.services.purchases import (
    TOTAL_TOLERANCE,
    computed_total,
    ensure_not_voided,
    get_purchase,
    next_seq,
    remove_line,
)
from app.services.resolution import resolve_line

_FOUR = Decimal("0.0001")


def _editable(purchase) -> None:
    ensure_not_voided(purchase)
    if purchase.status == "committed":
        raise ApiError(409, "committed", "Reopen the purchase before editing it.")


async def edit_header(db: AsyncSession, purchase_id: uuid.UUID, payload: PurchaseHeaderEdit):
    purchase = await get_purchase(db, purchase_id)
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
        for field, flag in (("purchased_at", "purchased_at_missing"), ("total", "total_missing"))
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
    header_tax = purchase.tax is not None and not any(
        line.line_kind == "tax" for line in purchase.lines
    )
    expected = computed_total(purchase) + (purchase.tax if header_tax else 0)
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
    purchase = await get_purchase(db, purchase_id)
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
    purchase = await get_purchase(db, purchase_id)
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
    purchase = await get_purchase(db, purchase_id)
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


async def re_resolve_line(db: AsyncSession, purchase_id: uuid.UUID, line_id: uuid.UUID):
    purchase = await get_purchase(db, purchase_id)
    ensure_not_voided(purchase)
    line = next((x for x in purchase.lines if x.id == line_id), None)
    if line is None:
        raise ApiError(404, "not_found", "No such line on this purchase.")
    line.resolved_by = None
    await resolve_line(db, purchase, line)
    await db.commit()
    return await get_purchase(db, purchase_id)
