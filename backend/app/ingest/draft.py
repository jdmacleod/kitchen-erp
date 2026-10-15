"""The draft purchase a receipt's lines stage writes, or rewrites on a retry."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.ingest.errors import StageFailure
from app.ingest.lines import ParsedLine
from app.models import IngestJob, Purchase, PurchaseLine, ReceiptDocument
from app.services.normalize import normalize_receipt_text


async def upsert_draft_purchase(
    db: AsyncSession,
    job: IngestJob,
    document: ReceiptDocument,
    *,
    purchased_at: datetime,
    subtotal: Decimal | None,
    tax: Decimal | None,
    total: Decimal,
    vendor_location_id: uuid.UUID | None,
    flags: list[str],
    lines: list[ParsedLine],
    printed_checks: dict[str, Any] | None = None,
) -> Purchase:
    """Create the receipt's draft purchase, or rewrite the one the job already has."""
    purchase: Purchase | None = None
    if job.purchase_id is not None:
        purchase = await db.get(Purchase, job.purchase_id)
    if purchase is not None:
        if purchase.status != "draft":
            raise StageFailure(code="purchase_not_draft")
        await db.execute(delete(PurchaseLine).where(PurchaseLine.purchase_id == purchase.id))
        purchase.purchased_at = purchased_at
        purchase.subtotal = subtotal
        purchase.tax = tax
        purchase.total = total
        purchase.vendor_location_id = vendor_location_id
        purchase.flags = list(flags)
        purchase.printed_checks = printed_checks
    else:
        purchase = Purchase(
            id=new_id(),
            vendor_location_id=vendor_location_id,
            receipt_document_id=document.id,
            purchased_at=purchased_at,
            subtotal=subtotal,
            tax=tax,
            total=total,
            status="draft",
            source="receipt",
            entered_by=document.uploaded_by,
            flags=list(flags),
            printed_checks=printed_checks,
        )
        db.add(purchase)
        await db.flush()  # the purchase row must exist before the job points at it
        job.purchase_id = purchase.id
    await db.flush()

    by_seq: dict[int, uuid.UUID] = {}
    for line in lines:
        line.purchase_line_id = new_id()
        by_seq[line.seq] = line.purchase_line_id
    for line in lines:
        db.add(
            PurchaseLine(
                id=line.purchase_line_id,
                purchase_id=purchase.id,
                seq=line.seq,
                raw_text=line.raw_text,
                raw_text_norm=normalize_receipt_text(line.raw_text),
                line_kind=line.line_kind,
                parent_line_id=None if line.parent_seq is None else by_seq.get(line.parent_seq),
                qty=line.qty,
                unit=line.unit,
                unit_price=line.unit_price,
                line_total=line.line_total,
                resolution="unmatched",
                flags=list(line.flags),
                printed_text=line.printed_row,
                in_price_amount=line.in_price_amount,
            )
        )
    await db.flush()
    return purchase
