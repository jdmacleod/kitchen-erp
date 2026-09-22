"""Bulk import of retailer purchase exports (Phase 2G).

Format: `kitchen-erp-purchase-export/1`, a JSON document with decimal strings:

    {"format": "kitchen-erp-purchase-export/1",
     "retailer": "Invented Mart",            # vendor name; created as a chain if unknown
     "transactions": [{
        "ref": "T-000123",                     # idempotency key within this retailer
        "store_code": "0417",                  # matched against receipt_identifiers
        "occurred_at": "2026-03-04T18:22:00",  # HOUSEHOLD_TIMEZONE local unless zoned
        "total": "23.45",
        "lines": [{"description": "ORG BANANAS", "upc": null, "qty": "1.32", "unit": "lb",
                   "amount": "1.83", "loyalty_amount": "-0.20"}]}]}

Amounts are read from the strings, never from floats. Lines resolve through the
same ladder as receipts (barcode first, then alias); the rest join the queue.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ApiError
from app.models import AppUser, Purchase, PurchaseLine
from app.models.geo import Vendor, VendorLocation
from app.services.normalize import normalize_receipt_text
from app.services.resolution import commit_purchase, resolve_purchase

FORMAT = "kitchen-erp-purchase-export/1"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


def _decimal(value: Any) -> Decimal:
    if isinstance(value, float):
        raise ValueError("amounts must be strings, not floating-point numbers")
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"not a decimal: {value!r}") from exc


class ExportLine(_Strict):
    description: str = Field(min_length=1)
    upc: str | None = None
    qty: Decimal | None = None
    unit: str | None = None
    amount: Decimal
    loyalty_amount: Decimal | None = None

    @field_validator("qty", "amount", "loyalty_amount", mode="before")
    @classmethod
    def _dec(cls, v):
        return None if v is None else _decimal(v)


class ExportTransaction(_Strict):
    ref: str = Field(min_length=1)
    store_code: str | None = None
    occurred_at: datetime
    total: Decimal | None = None
    lines: list[ExportLine] = Field(min_length=1)

    @field_validator("total", mode="before")
    @classmethod
    def _dec(cls, v):
        return None if v is None else _decimal(v)


class Export(_Strict):
    format: str
    retailer: str = Field(min_length=1)
    transactions: list[ExportTransaction]

    @field_validator("format")
    @classmethod
    def _fmt(cls, v):
        if v != FORMAT:
            raise ValueError(f"unsupported format {v!r}; expected {FORMAT!r}")
        return v


def load_export(path: Path) -> Export:
    try:
        data = json.loads(path.read_text(encoding="utf-8"), parse_float=str)
        return Export.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ApiError(422, "bad_export", f"Export file is not valid: {exc}") from exc


def _instant(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=ZoneInfo(get_settings().household_timezone))
    return value


async def _vendor(db: AsyncSession, name: str) -> Vendor:
    vendor = (
        await db.execute(select(Vendor).where(func.lower(Vendor.name) == name.lower()))
    ).scalar_one_or_none()
    if vendor is None:
        vendor = Vendor(name=name, kind="chain", price_scope="chain")
        db.add(vendor)
        await db.flush()
    return vendor


async def _location(
    db: AsyncSession, vendor: Vendor, store_code: str | None, fallback: uuid.UUID | None
) -> VendorLocation | None:
    if store_code:
        row = (
            (
                await db.execute(
                    select(VendorLocation).where(
                        VendorLocation.vendor_id == vendor.id,
                        VendorLocation.receipt_identifiers.any(store_code),
                    )
                )
            )
            .scalars()
            .first()
        )
        if row is not None:
            return row
    if fallback is not None:
        return await db.get(VendorLocation, fallback)
    locations = list(
        (
            await db.execute(select(VendorLocation).where(VendorLocation.vendor_id == vendor.id))
        ).scalars()
    )
    return locations[0] if len(locations) == 1 else None


def _line(seq: int, raw: str, kind: str, total: Decimal, **extra: Any) -> PurchaseLine:
    return PurchaseLine(
        seq=seq,
        raw_text=raw,
        raw_text_norm=normalize_receipt_text(raw),
        line_kind=kind,
        line_total=total,
        resolution="unmatched",
        flags=[],
        suggestions=[],
        **extra,
    )


async def import_export(
    db: AsyncSession,
    user: AppUser,
    export: Export,
    *,
    fallback_location_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Create one committed purchase per transaction. Idempotent on (retailer, ref)."""
    vendor = await _vendor(db, export.retailer)
    created = skipped = unlocated = 0
    for txn in export.transactions:
        import_ref = f"{export.retailer}:{txn.ref}"
        exists = (
            await db.execute(select(Purchase.id).where(Purchase.import_ref == import_ref))
        ).scalar_one_or_none()
        if exists is not None:
            skipped += 1
            continue
        location = await _location(db, vendor, txn.store_code, fallback_location_id)
        if location is None:
            unlocated += 1
            continue
        purchase = Purchase(
            vendor_location_id=location.id,
            purchased_at=_instant(txn.occurred_at),
            status="draft",
            source="import",
            entered_by=user.id,
            flags=[],
            import_ref=import_ref,
            total=Decimal("0"),
        )
        db.add(purchase)
        seq = 0
        running = Decimal("0")
        pending_discounts: list[tuple[PurchaseLine, PurchaseLine]] = []
        for line in txn.lines:
            seq += 1
            raw = f"{line.upc} {line.description}".strip() if line.upc else line.description
            item = _line(seq, raw, "item", line.amount, qty=line.qty, unit=line.unit)
            purchase.lines.append(item)
            running += line.amount
            if line.loyalty_amount:
                seq += 1
                discount = _line(seq, "LOYALTY", "discount", -abs(line.loyalty_amount))
                purchase.lines.append(discount)
                running -= abs(line.loyalty_amount)
                pending_discounts.append((discount, item))
        purchase.total = txn.total if txn.total is not None else running
        await db.flush()
        for discount, item in pending_discounts:
            discount.parent_line_id = item.id
        await db.flush()
        await resolve_purchase(db, purchase.id)
        await commit_purchase(db, user, purchase.id)
        created += 1
    await db.commit()
    return {"created": created, "skipped": skipped, "unlocated": unlocated}
