"""Row boxes on the receipt photo: found after reading, shown in review (04, 2S; issue 380).

The worker finds them when it has nothing else to do, for every receipt whose
reading is done (in review or committed) and whose latest OCR text has none
yet, newest first, so a receipt just read gets its boxes before older ones are
caught up. ``kerp receipts row-boxes`` does the same at once. Each finding is
a new ``row_boxes`` stage result naming the OCR result it matched against:
stage results are append-only, so nothing is ever edited, and a later reading
of the text simply needs boxes of its own.

Nothing here can hold up or fail a reading. Without Tesseract nothing runs; a
receipt it cannot read is left without boxes (and not tried again until the
worker restarts), and review shows no outlines.
"""

from __future__ import annotations

import shutil
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import bindparam, select, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.ingest import row_boxes
from app.ingest.paths import document_path
from app.ingest.printed_rows import receipt_rows
from app.ingest.stages import latest_results
from app.models import IngestJob, IngestStageResult, PurchaseLine, ReceiptDocument
from app.services.purchases import get_purchase

log = get_logger(__name__)

# The latest OCR text of each read receipt with no boxes found against it yet.
_NEEDING = text(
    """
    WITH latest_ocr AS (
        SELECT DISTINCT ON (r.job_id) r.job_id, r.id
        FROM ingest_stage_result r
        WHERE r.stage = 'ocr' AND r.output ? 'text'
        ORDER BY r.job_id, r.created_at DESC, r.id DESC
    )
    SELECT o.job_id
    FROM latest_ocr o
    JOIN ingest_job j ON j.id = o.job_id
    WHERE j.stage IN ('review', 'committed') AND j.status <> 'discarded'
      AND NOT (j.id = ANY(:skip))
      AND NOT EXISTS (
        SELECT 1 FROM ingest_stage_result b
        WHERE b.job_id = o.job_id
          AND b.stage = 'row_boxes'
          AND b.output ->> 'ocr_result_id' = o.id::text
      )
    ORDER BY j.created_at DESC, j.id DESC
    LIMIT :limit
    """
).bindparams(bindparam("skip", type_=ARRAY(UUID(as_uuid=True))))


def tesseract_installed() -> bool:
    return shutil.which(get_settings().tesseract_command) is not None


async def jobs_needing_boxes(
    db: AsyncSession, *, skip: set[uuid.UUID] | None = None, limit: int = 1
) -> list[uuid.UUID]:
    rows = await db.execute(_NEEDING, {"skip": list(skip or ()), "limit": limit})
    return [row[0] for row in rows]


async def find_for_job(db: AsyncSession, job_id: uuid.UUID) -> IngestStageResult | None:
    """Find and record the row boxes for a job's latest OCR text. Commits.

    Returns None, recording nothing, when there is no OCR text or Tesseract
    cannot read the stored image.
    """
    job = await db.get(IngestJob, job_id)
    ocr = (await latest_results(db, job_id)).get("ocr")
    receipt_text = None if ocr is None else ocr.output.get("text")
    if job is None or ocr is None or not isinstance(receipt_text, str):
        return None
    document = await db.get(ReceiptDocument, job.receipt_document_id)
    if document is None or not document_path(document).is_file():
        return None
    started = time.perf_counter()
    command = get_settings().tesseract_command
    try:
        found, width, height = await row_boxes.find_lines(
            document_path(document), document.mime, command
        )
    except row_boxes.BoxesUnavailable as exc:
        log.info("row boxes not found", extra={"job_id": str(job_id), "reason": str(exc)})
        return None
    output = row_boxes.boxes_output(receipt_text, found, width, height)
    result = IngestStageResult(
        job_id=job_id,
        stage=row_boxes.STAGE,
        adapter="tesseract",
        adapter_version=row_boxes.ROW_BOXES_VERSION,
        output={"ocr_result_id": str(ocr.id), **output},
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
    db.add(result)
    await db.commit()
    log.info(
        "row boxes found",
        extra={"job_id": str(job_id), "rows": output["row_count"], "boxes": len(output["rows"])},
    )
    return result


async def run_once(db: AsyncSession, failed: set[uuid.UUID]) -> bool:
    """Find boxes for one receipt that needs them. False when there is none to do.

    ``failed`` holds the jobs Tesseract could not read in this process, so the
    worker does not try them again on every idle poll.
    """
    if not tesseract_installed():
        return False
    pending = await jobs_needing_boxes(db, skip=failed)
    if not pending:
        return False
    job_id = pending[0]
    try:
        found = await find_for_job(db, job_id)
    except Exception as exc:
        # Never the worker's problem: whatever went wrong, the receipt simply
        # has no outlines, and the worker goes on to its other work.
        await db.rollback()
        log.warning(
            "row boxes failed", extra={"job_id": str(job_id), "exc_type": type(exc).__name__}
        )
        found = None
    if found is None:
        failed.add(job_id)
    return True


@dataclass(frozen=True)
class LineBox:
    line_id: uuid.UUID
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True)
class PurchaseBoxes:
    width: int | None = None
    height: int | None = None
    lines: tuple[LineBox, ...] = ()


async def boxes_for_purchase(db: AsyncSession, purchase_id: uuid.UUID) -> PurchaseBoxes:
    """The box of each line's printed row, found against the receipt's latest OCR text.

    Boxes found against an earlier OCR text are not used: their row numbers
    belong to other text. A line with no printed row, or a row with no box,
    has none.
    """
    await get_purchase(db, purchase_id)
    job_id = (
        await db.execute(
            select(IngestJob.id)
            .where(IngestJob.purchase_id == purchase_id)
            .order_by(IngestJob.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if job_id is None:
        return PurchaseBoxes()
    latest = await latest_results(db, job_id)
    ocr, found = latest.get("ocr"), latest.get(row_boxes.STAGE)
    if ocr is None or found is None or found.output.get("ocr_result_id") != str(ocr.id):
        return PurchaseBoxes()
    rows = receipt_rows(str(ocr.output.get("text") or ""))
    by_row = {box["row"]: box for box in found.output.get("rows", [])}
    lines = (
        await db.execute(
            select(PurchaseLine.id, PurchaseLine.printed_text, PurchaseLine.raw_text)
            .where(PurchaseLine.purchase_id == purchase_id, PurchaseLine.removed_at.is_(None))
            .order_by(PurchaseLine.seq)
        )
    ).all()
    indexes = row_boxes.line_rows([(line[1], line[2]) for line in lines], rows)
    boxed = tuple(
        LineBox(line[0], b["left"], b["top"], b["width"], b["height"])
        for line, row in zip(lines, indexes, strict=True)
        if row is not None and (b := by_row.get(row)) is not None
    )
    return PurchaseBoxes(found.output["width"], found.output["height"], boxed)
