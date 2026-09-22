"""Receipt upload, ingest job queries, retry, and conversion to manual entry.

The image is stored content-addressed at ``<RECEIPTS_PATH>/<aa>/<bb>/<sha256>.<ext>``
where the extension comes from the sniffed MIME type, never from the client's
file name or declared content type. ``receipt_document`` is immutable after
insert; a second upload of the same bytes returns the existing document and
job (criterion 16).
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.ids import new_id
from app.core.logging import get_logger
from app.ingest.stages import RUNNABLE_STAGES, STAGE_ORDER, latest_results
from app.models import (
    AppUser,
    IngestJob,
    IngestStageResult,
    Purchase,
    PurchaseLine,
    ReceiptDocument,
)
from app.models.geo import point_expr

log = get_logger(__name__)

EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/heic": "heic",
    "image/webp": "webp",
    "application/pdf": "pdf",
}
_HEIC_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}


def sniff_mime(data: bytes) -> str | None:
    """The MIME type from the bytes themselves; None when it is not an accepted type."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in _HEIC_BRANDS:
        return "image/heic"
    return None


def document_path(document: ReceiptDocument) -> Path:
    """Absolute path of the stored image. Built from the digest, not the stored string."""
    return Path(get_settings().receipts_path) / document.image_path


def relative_path(sha256: str, mime: str) -> str:
    return f"{sha256[:2]}/{sha256[2:4]}/{sha256}.{EXTENSIONS[mime]}"


@dataclass
class UploadResult:
    document: ReceiptDocument
    job: IngestJob
    created: bool


async def upload_receipt(
    db: AsyncSession,
    *,
    user: AppUser,
    data: bytes,
    ocr_text: str | None,
    captured_at: datetime | None,
    lat: Decimal | None,
    lon: Decimal | None,
) -> UploadResult:
    settings = get_settings()
    if len(data) > settings.receipt_max_bytes:
        raise ApiError(413, "payload_too_large", "The receipt image exceeds the size limit.")
    if not data:
        raise ApiError(422, "validation_error", "The image is empty.")
    mime = sniff_mime(data)
    if mime is None:
        raise ApiError(
            415,
            "unsupported_media_type",
            "Accepted receipt formats are JPEG, PNG, HEIC, WebP and PDF.",
        )
    if (lat is None) != (lon is None):
        raise ApiError(422, "validation_error", "lat and lon must be given together.")
    if lat is not None and lon is not None and not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ApiError(422, "validation_error", "lat/lon are outside the valid range.")

    digest = hashlib.sha256(data).hexdigest()
    existing = (
        await db.execute(select(ReceiptDocument).where(ReceiptDocument.sha256 == digest))
    ).scalar_one_or_none()
    if existing is not None:
        job = (
            await db.execute(select(IngestJob).where(IngestJob.receipt_document_id == existing.id))
        ).scalar_one()
        return UploadResult(existing, job, created=False)

    rel = relative_path(digest, mime)
    target = Path(settings.receipts_path) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        tmp = target.with_suffix(target.suffix + f".{uuid.uuid4().hex}.part")
        tmp.write_bytes(data)
        tmp.replace(target)

    document = ReceiptDocument(
        id=new_id(),
        sha256=digest,
        image_path=rel,
        mime=mime,
        bytes=len(data),
        captured_at=captured_at,
        capture_geo=point_expr(lat, lon) if lat is not None and lon is not None else None,
        client_ocr_text=ocr_text if ocr_text and ocr_text.strip() else None,
        uploaded_by=user.id,
    )
    job = IngestJob(
        id=new_id(),
        receipt_document_id=document.id,
        stage="captured",
        status="pending",
        attempts=0,
    )
    db.add(document)
    await db.flush()  # the job's FK needs the document row first
    db.add(job)
    await db.commit()
    await db.refresh(document)
    await db.refresh(job)
    log.info("receipt uploaded", extra={"document_id": str(document.id), "job_id": str(job.id)})
    return UploadResult(document, job, created=True)


async def get_document(db: AsyncSession, document_id: uuid.UUID) -> ReceiptDocument:
    document = await db.get(ReceiptDocument, document_id)
    if document is None:
        raise ApiError(404, "not_found", "Receipt document not found.")
    return document


async def get_job(db: AsyncSession, job_id: uuid.UUID) -> IngestJob:
    job = await db.get(IngestJob, job_id, populate_existing=True)
    if job is None:
        raise ApiError(404, "not_found", "Ingest job not found.")
    return job


async def job_for_document(db: AsyncSession, document_id: uuid.UUID) -> IngestJob | None:
    return (
        await db.execute(select(IngestJob).where(IngestJob.receipt_document_id == document_id))
    ).scalar_one_or_none()


async def stage_results_for(db: AsyncSession, job_id: uuid.UUID) -> list[IngestStageResult]:
    """Latest result per stage, in pipeline order."""
    latest = await latest_results(db, job_id)
    return [latest[s] for s in STAGE_ORDER if s in latest]


async def list_jobs(
    db: AsyncSession,
    *,
    status: str | None = None,
    stage: str | None = None,
    cursor: uuid.UUID | None = None,
    limit: int = 50,
) -> tuple[list[IngestJob], uuid.UUID | None]:
    stmt = select(IngestJob).order_by(IngestJob.id.desc()).limit(limit + 1)
    if status is not None:
        stmt = stmt.where(IngestJob.status == status)
    if stage is not None:
        stmt = stmt.where(IngestJob.stage == stage)
    if cursor is not None:
        stmt = stmt.where(IngestJob.id < cursor)
    rows = list((await db.execute(stmt)).scalars())
    next_cursor = rows[limit - 1].id if len(rows) > limit else None
    return rows[:limit], next_cursor


async def retry_job(db: AsyncSession, job_id: uuid.UUID) -> IngestJob:
    """A failed job goes back to pending at the stage that failed (criterion 25)."""
    job = await get_job(db, job_id)
    if job.status != "failed":
        raise ApiError(409, "job_not_failed", "Only a failed job can be retried.")
    if job.stage not in RUNNABLE_STAGES:
        raise ApiError(409, "job_not_runnable", "This job's stage is not run by the worker.")
    job.status = "pending"
    job.attempts = 0
    job.last_error = None
    job.next_attempt_at = None
    job.locked_at = None
    job.locked_by = None
    await db.commit()
    await db.refresh(job)
    return job


async def convert_to_manual(
    db: AsyncSession, job_id: uuid.UUID, user: AppUser
) -> tuple[IngestJob, Purchase]:
    """Give up on automatic parsing: a draft manual purchase linked to the document.

    Reuses the job's draft receipt purchase when one exists (its lines are
    dropped and its source becomes ``manual``), so no orphan draft is left
    behind; otherwise creates one from what the header stage found. The job
    ends ``done`` at ``committed`` with ``purchase_id`` set.
    """
    job = await get_job(db, job_id)
    if job.status in ("done", "running"):
        raise ApiError(409, "job_not_convertible", "This job is running or already finished.")
    document = await get_document(db, job.receipt_document_id)
    latest = await latest_results(db, job.id)
    header = latest["header"].output if "header" in latest else {}
    location = header.get("location") or {}
    purchased_at = (
        datetime.fromisoformat(header["purchased_at"])
        if header.get("purchased_at")
        else document.captured_at or document.created_at or datetime.now(UTC)
    )
    total = Decimal(str(header["total"])) if header.get("total") is not None else Decimal("0")
    vendor_location_id = (
        uuid.UUID(location["vendor_location_id"]) if location.get("vendor_location_id") else None
    )

    purchase = await db.get(Purchase, job.purchase_id) if job.purchase_id is not None else None
    if purchase is not None and purchase.status == "draft":
        await db.execute(delete(PurchaseLine).where(PurchaseLine.purchase_id == purchase.id))
        purchase.source = "manual"
        purchase.flags = [f for f in purchase.flags if f != "reconcile_mismatch"]
    else:
        purchase = Purchase(
            id=new_id(),
            vendor_location_id=vendor_location_id,
            receipt_document_id=document.id,
            purchased_at=purchased_at,
            subtotal=Decimal(str(header["subtotal"]))
            if header.get("subtotal") is not None
            else None,
            tax=Decimal(str(header["tax"])) if header.get("tax") is not None else None,
            total=total,
            status="draft",
            source="manual",
            entered_by=user.id,
            flags=[],
        )
        db.add(purchase)
        await db.flush()  # the purchase row must exist before the job points at it
        job.purchase_id = purchase.id
    job.stage = "committed"
    job.status = "done"
    job.last_error = None
    job.next_attempt_at = None
    job.locked_at = None
    job.locked_by = None
    await db.commit()
    await db.refresh(job)
    await db.refresh(purchase)
    return job, purchase
