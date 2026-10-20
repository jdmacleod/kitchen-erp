"""The to-identify queue (04, 2D): unmatched item lines on committed purchases.

Grouped by vendor and normalized wording, so one answer applies to every line
that reads alike. Answering goes through ``resolution.decide_line``, as review
does: a product, ignore, or a kitchen supply (2T), each learned as an alias.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import receipt_codes
from app.ingest.lines import ZERO_ROW_FLAGS
from app.models import AppUser, Purchase, PurchaseLine
from app.models.geo import Vendor, VendorLocation
from app.services.resolution import decide_line


async def to_identify(db: AsyncSession) -> list[dict[str, Any]]:
    rows = await db.execute(
        text(
            """
            SELECT v.id AS vendor_id, v.name AS vendor_name, pl.raw_text_norm,
                   count(*) AS line_count,
                   json_agg(json_build_object(
                       'line_id', pl.id, 'purchase_id', p.id, 'raw_text', pl.raw_text,
                       'purchased_at', p.purchased_at, 'line_total', pl.line_total::text,
                       'qty', pl.qty::text, 'unit', pl.unit
                   ) ORDER BY p.purchased_at DESC) AS lines
            FROM purchase_line pl
            JOIN purchase p ON p.id = pl.purchase_id
            JOIN vendor_location vl ON vl.id = p.vendor_location_id
            JOIN vendor v ON v.id = vl.vendor_id
            WHERE p.status = 'committed' AND pl.line_kind = 'item'
              AND pl.resolution = 'unmatched' AND pl.removed_at IS NULL
              AND NOT pl.flags && CAST(:zero AS varchar[])
            GROUP BY v.id, v.name, pl.raw_text_norm
            ORDER BY max(p.purchased_at) DESC
            """
        ),
        {"zero": list(ZERO_ROW_FLAGS)},
    )
    return [dict(r) for r in rows.mappings()]


def elsewhere(groups: list[dict[str, Any]]) -> dict[tuple[str, str | None], list[dict]]:
    """Per group, the other vendors where the same wording waits, most lines first:
    one answer may serve them all, though each vendor learns its own wording."""
    by_text: dict[str, list[dict[str, Any]]] = {}
    for g in groups:
        if g["raw_text_norm"]:
            by_text.setdefault(g["raw_text_norm"], []).append(g)
    out: dict[tuple[str, str | None], list[dict]] = {}
    for same in by_text.values():
        if len(same) < 2:
            continue
        for g in same:
            out[(str(g["vendor_id"]), g["raw_text_norm"])] = [
                {
                    "vendor_id": o["vendor_id"],
                    "vendor_name": o["vendor_name"],
                    "line_count": o["line_count"],
                }
                for o in sorted(same, key=lambda o: (-o["line_count"], o["vendor_name"]))
                if o is not g
            ]
    return out


def _queued(vendor_id: uuid.UUID, raw_text_norm: str):
    return (
        select(PurchaseLine.id, PurchaseLine.purchase_id)
        .join(Purchase, Purchase.id == PurchaseLine.purchase_id)
        .join(VendorLocation, VendorLocation.id == Purchase.vendor_location_id)
        .where(
            Purchase.status == "committed",
            PurchaseLine.line_kind == "item",
            PurchaseLine.resolution == "unmatched",
            PurchaseLine.raw_text_norm == raw_text_norm,
            PurchaseLine.removed_at.is_(None),
            ~PurchaseLine.flags.overlap(list(ZERO_ROW_FLAGS)),
            VendorLocation.vendor_id == vendor_id,
        )
    )


async def queue_codes(
    db: AsyncSession, groups: list[dict[str, Any]]
) -> dict[tuple[str, str | None], dict[str, str]]:
    """Per to-identify group, the code its lines carry, read with its vendor's layout."""
    out: dict[tuple[str, str | None], dict[str, str]] = {}
    vendors: dict[str, Vendor | None] = {}
    for group in groups:
        key = str(group["vendor_id"])
        if key not in vendors:
            vendors[key] = await db.get(Vendor, uuid.UUID(key))
        vendor = vendors[key]
        if vendor is None or not group["lines"]:
            continue
        raw = group["lines"][0].get("raw_text") or ""
        offer = receipt_codes.read(raw, vendor.code_position, vendor.rw_layout).offer
        if offer is not None:
            out[(key, group["raw_text_norm"])] = {"scheme": offer[0], "value": offer[1]}
    return out


async def queued_line_ids(
    db: AsyncSession, vendor_id: uuid.UUID, raw_text_norm: str
) -> list[uuid.UUID]:
    """The group's lines still waiting in the to-identify queue."""
    return [lid for lid, _ in (await db.execute(_queued(vendor_id, raw_text_norm))).all()]


async def apply_to_identify(
    db: AsyncSession,
    user: AppUser,
    *,
    vendor_id: uuid.UUID,
    raw_text_norm: str,
    product_id: uuid.UUID | None,
    ignore: bool,
    line_ids: list[uuid.UUID] | None,
    supply_category: str | None = None,
) -> int:
    """Identify one queued line and, unless narrowed, every other queued line with
    the same vendor and normalized text. Each emits an observation dated to its
    purchase; a kitchen supply or an ignored line emits none."""
    rows = (await db.execute(_queued(vendor_id, raw_text_norm))).all()
    targets = [(lid, pid) for lid, pid in rows if line_ids is None or lid in set(line_ids)]
    for line_id, purchase_id in targets:
        await decide_line(
            db,
            user,
            purchase_id,
            line_id,
            product_id=product_id,
            ignore=ignore,
            supply_category=supply_category,
        )
    return len(targets)
