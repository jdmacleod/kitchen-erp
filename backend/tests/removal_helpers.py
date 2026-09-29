"""Receipt purchases for the removal tests (#74), without running the reader.

A receipt is uploaded through the API; its draft is then written directly, as
the lines stage would, so each test can put the job in the state it needs.
Invented vendors and synthetic images only.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx

from app.core.db import get_sessionmaker
from app.models import IngestJob, Purchase, PurchaseLine, ReceiptDocument
from tests.ingest_helpers import png_bytes, upload

WHEN = datetime(2026, 7, 9, 18, 30, tzinfo=UTC)


async def upload_receipt(client: httpx.AsyncClient, seed: str) -> dict:
    r = await upload(client, png_bytes(seed))
    assert r.status_code == 201, r.text
    return r.json()


def stored_photo(receipts_dir: Path, document: dict) -> Path:
    sha = document["sha256"]
    return receipts_dir / sha[:2] / sha[2:4] / f"{sha}.png"


async def receipt_draft(
    client: httpx.AsyncClient,
    seed: str,
    *,
    job_status: str = "needs_review",
    location_id: str | None = None,
    product_id: str | None = None,
) -> tuple[dict, str]:
    """An uploaded receipt with a one-line draft; returns (upload body, purchase id)."""
    body = await upload_receipt(client, seed)
    async with get_sessionmaker()() as db:
        job = await db.get(IngestJob, uuid.UUID(body["job"]["id"]))
        document = await db.get(ReceiptDocument, job.receipt_document_id)
        purchase = Purchase(
            receipt_document_id=job.receipt_document_id,
            vendor_location_id=uuid.UUID(location_id) if location_id else None,
            purchased_at=WHEN,
            total=Decimal("4.2500"),
            status="draft",
            source="receipt",
            entered_by=document.uploaded_by,
            flags=[],
        )
        purchase.lines.append(
            PurchaseLine(
                seq=1,
                raw_text="ORCHARD PEARS",
                raw_text_norm="orchard pears",
                line_kind="item",
                product_id=uuid.UUID(product_id) if product_id else None,
                qty=Decimal("1"),
                unit="each",
                unit_price=Decimal("4.2500"),
                line_total=Decimal("4.2500"),
                resolution="manual" if product_id else "unmatched",
                flags=[],
                suggestions=[],
            )
        )
        db.add(purchase)
        await db.flush()
        job.purchase_id = purchase.id
        job.status = job_status
        job.stage = "review" if job_status == "needs_review" else "lines"
        await db.commit()
        return body, str(purchase.id)


async def set_job_status(job_id: str, status: str) -> None:
    async with get_sessionmaker()() as db:
        job = await db.get(IngestJob, uuid.UUID(job_id))
        job.status = status
        await db.commit()
