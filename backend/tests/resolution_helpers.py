"""Build draft receipt purchases directly, so the ladder is testable without the pipeline."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from app.core.db import get_sessionmaker
from app.models import Purchase, PurchaseLine


async def make_receipt_purchase(
    user_id: uuid.UUID,
    location_id: str,
    lines: list[dict],
    *,
    purchased_at: datetime | None = None,
    total: str | None = None,
) -> str:
    """lines: dicts with raw_text, line_total and optional line_kind, qty, unit,
    unit_price, flags, and parent (a seq)."""
    async with get_sessionmaker()() as db:
        purchase = Purchase(
            vendor_location_id=uuid.UUID(location_id),
            purchased_at=purchased_at or datetime(2026, 5, 2, 17, 30, tzinfo=UTC),
            total=Decimal(total) if total else sum(Decimal(ln["line_total"]) for ln in lines),
            status="draft",
            source="receipt",
            entered_by=user_id,
            flags=[],
        )
        by_seq: dict[int, PurchaseLine] = {}
        for seq, spec in enumerate(lines, start=1):
            line = PurchaseLine(
                seq=seq,
                raw_text=spec["raw_text"],
                line_kind=spec.get("line_kind", "item"),
                qty=Decimal(spec["qty"]) if spec.get("qty") else None,
                unit=spec.get("unit"),
                unit_price=Decimal(spec["unit_price"]) if spec.get("unit_price") else None,
                line_total=Decimal(spec["line_total"]),
                resolution="unmatched",
                flags=list(spec.get("flags", [])),
                suggestions=[],
            )
            purchase.lines.append(line)
            by_seq[seq] = line
        db.add(purchase)
        await db.flush()
        for seq, spec in enumerate(lines, start=1):
            if spec.get("parent"):
                by_seq[seq].parent_line_id = by_seq[spec["parent"]].id
        await db.commit()
        return str(purchase.id)
