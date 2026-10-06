"""Upload batches (issue 122): what a batch of receipts came to, and how long reading takes.

A batch is one upload: one receipt, or several chosen at once. The browser
opens the batch with the number of files the person chose, then sends each
file with the batch's id; ``app.services.ingest.upload_receipt`` records what
became of each (read as new, read again after a removal, or already seen).

Each receipt's trust comes from :func:`app.services.purchases.assess`, the rule
the purchase API and the inbox use, so the three cannot disagree.
"""

from __future__ import annotations

import math
import statistics
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.ids import new_id
from app.models import AppUser, IngestJob, UploadBatch, UploadBatchReceipt
from app.services import purchases

# The most files one batch may announce; the upload form has no lower cap of its own.
MAX_FILES = 500

# Read times are taken from this many of the most recently finished reads.
ESTIMATE_SAMPLE = 10


async def create_batch(db: AsyncSession, *, user: AppUser, file_count: int) -> UploadBatch:
    if not 1 <= file_count <= MAX_FILES:
        raise ApiError(422, "validation_error", f"A batch holds between 1 and {MAX_FILES} files.")
    batch = UploadBatch(id=new_id(), created_by=user.id, file_count=file_count)
    db.add(batch)
    await db.commit()
    await db.refresh(batch)
    return batch


async def get_batch(db: AsyncSession, batch_id: uuid.UUID) -> UploadBatch:
    batch = await db.get(UploadBatch, batch_id)
    if batch is None:
        raise ApiError(404, "batch_not_found", "Upload batch not found.")
    return batch


async def record(db: AsyncSession, batch_id: uuid.UUID, job: IngestJob, outcome: str) -> None:
    """What became of one file. The same file twice in one batch counts once."""
    await db.execute(
        insert(UploadBatchReceipt)
        .values(batch_id=batch_id, job_id=job.id, outcome=outcome)
        .on_conflict_do_nothing(index_elements=["batch_id", "job_id"])
    )


# One row per receipt in the listed batches: its outcome in the batch, its job,
# and the purchase it was read into, with the sums the trust rule needs.
_RECEIPTS_SQL = text(
    """
    SELECT e.batch_id, e.outcome, j.id AS job_id, j.status AS job_status, j.purchase_id,
           p.status AS purchase_status, p.source, p.total, p.tax, p.flags, p.purchased_at,
           v.name AS store,
           (SELECT count(*) FROM purchase_line pl
             WHERE pl.purchase_id = p.id AND pl.removed_at IS NULL
               AND pl.line_kind = 'item') AS item_lines,
           (SELECT coalesce(sum(CASE WHEN pl.line_kind = 'discount' THEN -abs(pl.line_total)
                                     ELSE pl.line_total END), 0)
              FROM purchase_line pl
             WHERE pl.purchase_id = p.id AND pl.removed_at IS NULL) AS lines_sum,
           (SELECT bool_or(pl.line_kind = 'tax') FROM purchase_line pl
             WHERE pl.purchase_id = p.id AND pl.removed_at IS NULL) AS has_tax_line,
           (SELECT count(*) FROM purchase_line pl
             WHERE pl.purchase_id = p.id AND pl.removed_at IS NULL
               AND 'not_in_scan' = ANY(pl.flags)) AS unscanned
    FROM upload_batch_receipt e
    JOIN ingest_job j ON j.id = e.job_id
    LEFT JOIN purchase p ON p.id = j.purchase_id
    LEFT JOIN vendor_location vl ON vl.id = p.vendor_location_id
    LEFT JOIN vendor v ON v.id = vl.vendor_id
    WHERE e.batch_id = ANY(:ids) AND j.status <> 'discarded'
    ORDER BY e.created_at, j.id
    """
)


@dataclass
class BatchReceipt:
    job: IngestJob
    outcome: str
    store: str | None = None
    total: Decimal | None = None
    item_lines: int | None = None
    lines_total: Decimal | None = None
    # adds_up / check_lines / couldnt_read once read; None while reading, or
    # for a receipt that ended up entered by hand.
    trust: str | None = None
    gap: Decimal | None = None
    held: bool = False


@dataclass
class BatchSummary:
    batch: UploadBatch
    receipts: list[BatchReceipt] = field(default_factory=list)

    def count(self, *, outcome: str | None = None, trust: str | None = None) -> int:
        return sum(
            (outcome is None or r.outcome == outcome) and (trust is None or r.trust == trust)
            for r in self.receipts
        )

    @property
    def reading(self) -> int:
        # A receipt seen before is being read, if at all, for its earlier batch.
        return sum(
            r.outcome != "already_seen" and r.job.status in ("pending", "running")
            for r in self.receipts
        )


def _receipt(r, job: IngestJob) -> BatchReceipt:
    item = BatchReceipt(job=job, outcome=r["outcome"])
    if r["job_status"] == "failed":
        item.trust = "couldnt_read"
        return item
    if r["purchase_id"] is None or r["job_status"] in ("pending", "running"):
        return item
    lines_sum = r["lines_sum"]
    if r["tax"] is not None and not r["has_tax_line"]:
        lines_sum += r["tax"]
    check = purchases.assess(
        source=r["source"],
        status=r["purchase_status"],
        flags=r["flags"] or [],
        lines_sum=lines_sum,
        total=r["total"],
        item_lines=r["item_lines"],
        unscanned=r["unscanned"],
    )
    item.store = r["store"]
    item.total = r["total"]
    item.item_lines = r["item_lines"]
    item.lines_total = check.lines_total
    item.trust = check.trust
    item.gap = purchases.reading_gap(check, r["total"])
    item.held = check.held
    return item


async def list_batches(
    db: AsyncSession, *, cursor: uuid.UUID | None = None, limit: int = 10
) -> tuple[list[BatchSummary], uuid.UUID | None]:
    """Newest first; a batch whose every receipt was removed is not listed."""
    rows = (
        await db.execute(
            text(
                """
                SELECT b.id FROM upload_batch b
                WHERE EXISTS (
                    SELECT 1 FROM upload_batch_receipt e JOIN ingest_job j ON j.id = e.job_id
                    WHERE e.batch_id = b.id AND j.status <> 'discarded')
                  AND (CAST(:cursor AS uuid) IS NULL OR b.id < CAST(:cursor AS uuid))
                ORDER BY b.id DESC
                LIMIT :n
                """
            ),
            {"cursor": cursor, "n": limit + 1},
        )
    ).scalars()
    ids = list(rows)
    next_cursor = ids[limit - 1] if len(ids) > limit else None
    ids = ids[:limit]
    if not ids:
        return [], None
    found = (await db.execute(select(UploadBatch).where(UploadBatch.id.in_(ids)))).scalars()
    batches = {b.id: BatchSummary(batch=b) for b in found}
    rows = list((await db.execute(_RECEIPTS_SQL, {"ids": ids})).mappings())
    job_ids = {r["job_id"] for r in rows}
    jobs = {
        j.id: j
        for j in (await db.execute(select(IngestJob).where(IngestJob.id.in_(job_ids)))).scalars()
    }
    for r in rows:
        batches[r["batch_id"]].receipts.append(_receipt(r, jobs[r["job_id"]]))
    return [batches[i] for i in ids], next_cursor


# Seconds each recently finished read spent in the worker's stages, newest first.
# Only the stage results since the job's last upload count, so a revived
# receipt's first reading does not inflate its second.
_READ_TIMES_SQL = text(
    """
    SELECT sum(r.duration_ms) / 1000.0 AS seconds
    FROM ingest_job j
    JOIN ingest_stage_result r ON r.job_id = j.id AND r.created_at >= j.uploaded_at
    WHERE j.status IN ('needs_review', 'done')
    GROUP BY j.id
    ORDER BY max(r.created_at) DESC
    LIMIT :n
    """
)

# Receipts in the batches still being read: how many there are, and how many are done.
_ACTIVE_SQL = text(
    """
    WITH active AS (
        SELECT DISTINCT e.batch_id FROM upload_batch_receipt e
        JOIN ingest_job j ON j.id = e.job_id
        WHERE e.outcome <> 'already_seen' AND j.status IN ('pending', 'running')
    )
    SELECT count(DISTINCT j.id) AS of,
           count(DISTINCT j.id) FILTER (WHERE j.status NOT IN ('pending', 'running')) AS done
    FROM upload_batch_receipt e
    JOIN active a ON a.batch_id = e.batch_id
    JOIN ingest_job j ON j.id = e.job_id
    WHERE e.outcome <> 'already_seen' AND j.status <> 'discarded'
    """
)


@dataclass
class ReadingProgress:
    done: int
    of: int
    # Whole minutes, at least 1; None until there are finished reads to go by.
    minutes_left: int | None


def minutes_left(remaining: int, recent_seconds: list[float]) -> int | None:
    """Remaining reads at the median recent pace, one worker; never under a minute."""
    if remaining <= 0 or not recent_seconds:
        return None
    return max(1, math.ceil(remaining * statistics.median(recent_seconds) / 60))


async def reading_progress(db: AsyncSession, reading: int) -> ReadingProgress | None:
    """How far the current batches have got, for Home's reading line."""
    if reading <= 0:
        return None
    row = (await db.execute(_ACTIVE_SQL)).mappings().one()
    times = [
        float(s) for s in (await db.execute(_READ_TIMES_SQL, {"n": ESTIMATE_SAMPLE})).scalars()
    ]
    of, done = row["of"], row["done"]
    if of < reading:
        # Receipts being read outside any batch still listed: count only those.
        of, done = reading, 0
    return ReadingProgress(done=done, of=of, minutes_left=minutes_left(reading, times))
