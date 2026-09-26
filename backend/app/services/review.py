"""Editing a receipt purchase in review: header, lines, attachments (Phase 2D)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.ingest.lines import QTY_FLAGS
from app.models import AppUser, Product, PurchaseLine
from app.models.geo import VendorLocation
from app.schemas.purchases import LineAdd, LineEdit, PurchaseHeaderEdit
from app.services import pricebook
from app.services.normalize import normalize_receipt_text
from app.services.purchases import get_purchase, live_observations
from app.services.resolution import resolve_line

_FOUR = Decimal("0.0001")


def _editable(purchase) -> None:
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
    await db.commit()
    return await get_purchase(db, purchase_id)


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
    seqs = [line.seq for line in purchase.lines]
    if payload.after_seq is None or payload.after_seq >= (max(seqs) if seqs else 0):
        seq = (max(seqs) if seqs else 0) + 1
    else:
        seq = payload.after_seq + 1
        for line in sorted(purchase.lines, key=lambda x: -x.seq):
            if line.seq >= seq:
                line.seq += 1
        await db.flush()
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
    await db.commit()
    return await get_purchase(db, purchase_id)


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
    # corrected from the print, or assumed (#31).
    if {"qty", "unit", "unit_price", "clear_qty"} & data.keys():
        line.flags = [f for f in line.flags if f not in QTY_FLAGS]
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
    await db.commit()
    return await get_purchase(db, purchase_id)


async def delete_line(db: AsyncSession, user: AppUser, purchase_id: uuid.UUID, line_id: uuid.UUID):
    purchase = await get_purchase(db, purchase_id)
    _editable(purchase)
    line = next((x for x in purchase.lines if x.id == line_id), None)
    if line is None:
        raise ApiError(404, "not_found", "No such line on this purchase.")
    live = await live_observations(db, purchase)
    if line.id in live:
        await pricebook.void(db, live[line.id], "line deleted in review", user)
    for other in purchase.lines:
        if other.parent_line_id == line.id:
            other.parent_line_id = None
    purchase.lines.remove(line)
    await db.commit()
    return await get_purchase(db, purchase_id)


async def re_resolve_line(db: AsyncSession, purchase_id: uuid.UUID, line_id: uuid.UUID):
    purchase = await get_purchase(db, purchase_id)
    line = next((x for x in purchase.lines if x.id == line_id), None)
    if line is None:
        raise ApiError(404, "not_found", "No such line on this purchase.")
    line.resolved_by = None
    await resolve_line(db, purchase, line)
    await db.commit()
    return await get_purchase(db, purchase_id)
