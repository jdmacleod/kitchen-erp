"""Remove metadata from receipt photos stored before #221.

`kerp receipts strip-metadata` runs this once after upgrading. Each receipt's file
is stripped as an upload now is (`image_metadata.strip`). When that changes it,
the stripped file is written at its new content address, and the row's
``sha256``, ``image_path``, ``mime`` and ``bytes`` change together, with
``upload_sha256`` keeping the old digest so a second upload of the original file
is still caught. The old file is deleted after the commit. A receipt keeps its
id, so its job, purchase and stage results stay linked.

It refuses to start while a stored file is missing (a removed receipt's photo is
deleted on purpose, so those are skipped), and it leaves alone a receipt whose
job is being read: run it again once the worker is idle.
"""

from __future__ import annotations

import hashlib
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.models import IngestJob, ReceiptDocument
from app.services import image_metadata
from app.services.ingest import relative_path

log = get_logger(__name__)


@dataclass
class Report:
    # outcome -> count: stripped, reencoded, unchanged, pdf, removed, in_use,
    # collision, unreadable
    counts: Counter[str] = field(default_factory=Counter)
    # Receipts whose stored file is missing; nothing is changed while any are.
    missing: list[str] = field(default_factory=list)
    # Receipts that would be (or were) stripped, by id.
    changed: list[str] = field(default_factory=list)
    reencoded: int = 0


def _root() -> Path:
    return Path(get_settings().receipts_path)


@dataclass(frozen=True)
class _Row:
    id: uuid.UUID
    image_path: str
    mime: str
    removed: bool


async def _rows(db: AsyncSession) -> list[_Row]:
    """Plain values, read once: each receipt's commit expires the ORM objects."""
    stmt = (
        select(
            ReceiptDocument.id, ReceiptDocument.image_path, ReceiptDocument.mime, IngestJob.status
        )
        .outerjoin(IngestJob, IngestJob.receipt_document_id == ReceiptDocument.id)
        .order_by(ReceiptDocument.created_at, ReceiptDocument.id)
    )
    rows = (await db.execute(stmt)).all()
    await db.rollback()  # end the read; each receipt takes its own lock below
    return [_Row(r.id, r.image_path, r.mime, r.status == "discarded") for r in rows]


async def run(db: AsyncSession, *, dry_run: bool) -> Report:
    report = Report()
    rows = await _rows(db)
    report.missing = [
        str(row.id) for row in rows if not row.removed and not (_root() / row.image_path).is_file()
    ]
    if report.missing:
        return report

    for row in rows:
        doc_id = row.id
        if row.removed:
            report.counts["removed"] += 1
            continue
        if row.mime == "application/pdf":
            report.counts["pdf"] += 1
            continue
        try:
            outcome = _plan(row) if dry_run else await _strip_one(db, doc_id)
        except image_metadata.Unreadable:
            await db.rollback()
            outcome = "unreadable"
        report.counts[outcome] += 1
        if outcome in ("stripped", "reencoded"):
            report.changed.append(str(doc_id))
            if outcome == "reencoded":
                report.reencoded += 1
    return report


def _plan(row: _Row) -> str:
    data = (_root() / row.image_path).read_bytes()
    result = image_metadata.strip(data, row.mime)
    if result.data == data:
        return "unchanged"
    return "reencoded" if result.reencoded else "stripped"


async def _strip_one(db: AsyncSession, document_id: uuid.UUID) -> str:
    """Strip one receipt under its job's lock (the lock an upload takes)."""
    job = (
        await db.execute(
            select(IngestJob).where(IngestJob.receipt_document_id == document_id).with_for_update()
        )
    ).scalar_one_or_none()
    document = await db.get(ReceiptDocument, document_id, populate_existing=True)
    if document is None:
        await db.rollback()
        return "removed"
    if job is not None and (job.status == "running" or job.locked_at is not None):
        await db.rollback()
        return "in_use"
    old = _root() / document.image_path
    data = old.read_bytes()
    result = image_metadata.strip(data, document.mime)
    if result.data == data:
        await db.rollback()
        return "unchanged"
    digest = hashlib.sha256(result.data).hexdigest()
    taken = await db.scalar(
        select(ReceiptDocument.id).where(
            ReceiptDocument.sha256 == digest, ReceiptDocument.id != document.id
        )
    )
    if taken is not None:
        await db.rollback()
        return "collision"
    rel = relative_path(digest, result.mime)
    new = _root() / rel
    new.parent.mkdir(parents=True, exist_ok=True)
    tmp = new.with_suffix(new.suffix + ".strip.part")
    tmp.write_bytes(result.data)
    tmp.replace(new)
    document.upload_sha256 = document.upload_sha256 or document.sha256
    document.sha256 = digest
    document.image_path = rel
    document.mime = result.mime
    document.bytes = len(result.data)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        if new != old:
            new.unlink(missing_ok=True)
        raise
    if new != old:
        old.unlink(missing_ok=True)
    log.info("receipt metadata removed", extra={"document_id": str(document_id)})
    return "reencoded" if result.reencoded else "stripped"
