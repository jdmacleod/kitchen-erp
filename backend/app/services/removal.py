"""Removing a purchase, or a receipt that could not be read (#74; spec 04, 2H),
and restoring a removed one (#210).

One rule decides what removal does, and the preview shown before it and the
action itself both ask it, so the two cannot disagree:

- nothing from the purchase ever reached the price book: it is deleted, its
  receipt's job becomes ``discarded``, and its photo is deleted;
- otherwise it is voided: its live prices are voided ("purchase removed") and
  it is kept, read-only, with its photo;
- while its receipt is still being read, it cannot be removed, because the
  reader would write the draft back.

A voided purchase can be restored to reviewed. Its voided prices stay voided
(observations are append-only); committing it again records new ones.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.core.logging import get_logger
from app.ingest.paths import document_path
from app.models import (
    AppUser,
    IngestJob,
    PriceObservation,
    PriceObservationVoid,
    Purchase,
    PurchaseLine,
    ReceiptDocument,
)
from app.services import pricebook
from app.services.purchases import get_purchase

log = get_logger(__name__)

READING = ("pending", "running")


@dataclass
class RemovalPlan:
    outcome: Literal["delete", "void"]
    prices: int
    photo: bool
    blocked: Literal["still_reading"] | None
    job: IngestJob | None = field(default=None, repr=False)
    live: list[uuid.UUID] = field(default_factory=list, repr=False)
    document: ReceiptDocument | None = field(default=None, repr=False)


@dataclass
class Removed:
    outcome: Literal["delete", "void"]
    photo_deleted: bool
    purchase: Purchase | None = None


VOID_REASON = "purchase removed"


async def prices_voided_by_removal(db: AsyncSession, purchase_id: uuid.UUID) -> int:
    """How many prices removing the purchase voided; not ones voided before it."""
    stmt = (
        select(func.count())
        .select_from(PriceObservationVoid)
        .join(PriceObservation, PriceObservation.id == PriceObservationVoid.observation_id)
        .join(PurchaseLine, PurchaseLine.id == PriceObservation.purchase_line_id)
        .where(PurchaseLine.purchase_id == purchase_id, PriceObservationVoid.reason == VOID_REASON)
    )
    return int((await db.execute(stmt)).scalar_one())


def still_reading() -> ApiError:
    return ApiError(409, "still_reading", "You can remove it once it's been read.")


async def _job_for(db: AsyncSession, purchase_id: uuid.UUID, *, lock: bool) -> IngestJob | None:
    stmt = select(IngestJob).where(IngestJob.purchase_id == purchase_id)
    if lock:
        # A retry or the worker changing the job while this decides would put
        # the draft back, or leave a pending job pointing at nothing.
        stmt = stmt.with_for_update()
    stmt = stmt.execution_options(populate_existing=True)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _observations(db: AsyncSession, purchase_id: uuid.UUID) -> tuple[bool, list[uuid.UUID]]:
    """Whether any line, removed ones included, ever reached the price book; and
    the observations still live."""
    stmt = (
        select(PriceObservation.id, PriceObservationVoid.id)
        .join(PurchaseLine, PurchaseLine.id == PriceObservation.purchase_line_id)
        .outerjoin(PriceObservationVoid, PriceObservationVoid.observation_id == PriceObservation.id)
        .where(PurchaseLine.purchase_id == purchase_id)
    )
    rows = (await db.execute(stmt)).all()
    return bool(rows), [obs for obs, void in rows if void is None]


async def _photo_to_delete(
    db: AsyncSession, document_id: uuid.UUID | None, leaving: uuid.UUID | None
) -> ReceiptDocument | None:
    """The receipt's stored photo, when nothing else will still show it."""
    if document_id is None:
        return None
    others = select(func.count()).where(Purchase.receipt_document_id == document_id)
    if leaving is not None:
        others = others.where(Purchase.id != leaving)
    if (await db.execute(others)).scalar_one():
        return None
    document = await db.get(ReceiptDocument, document_id)
    if document is None or not document_path(document).is_file():
        return None
    return document


async def removal_plan(db: AsyncSession, purchase: Purchase, *, lock: bool = False) -> RemovalPlan:
    job = await _job_for(db, purchase.id, lock=lock)
    blocked = "still_reading" if job is not None and job.status in READING else None
    recorded, live = await _observations(db, purchase.id)
    if recorded:
        return RemovalPlan("void", len(live), False, blocked, job, live)
    document = await _photo_to_delete(db, purchase.receipt_document_id, purchase.id)
    return RemovalPlan("delete", 0, document is not None, blocked, job, [], document)


def _discard(job: IngestJob) -> None:
    job.status = "discarded"
    job.purchase_id = None
    job.next_attempt_at = None
    job.locked_at = None
    job.locked_by = None


async def _delete(db: AsyncSession, purchase: Purchase, job: IngestJob | None) -> None:
    if job is not None:
        _discard(job)
        await db.flush()  # the job lets go of the purchase before it goes
    await db.delete(purchase)
    await db.flush()


async def delete_photo(db: AsyncSession, document: ReceiptDocument | None) -> bool:
    """Delete a removed receipt's photo, once the removal has committed.

    Only after the commit: a rollback cannot bring a file back. The receipt's
    job is locked and checked again first, because uploading the same file
    between the commit and here revives the job and writes the photo back;
    the upload takes the same lock. If the file can't be deleted, the removal
    still stands and the image route refuses a removed receipt anyway.
    """
    if document is None:
        return False
    stmt = (
        select(IngestJob.status)
        .where(IngestJob.receipt_document_id == document.id)
        .with_for_update()
    )
    status = (await db.execute(stmt)).scalar_one_or_none()
    try:
        if status != "discarded":
            return False
        document_path(document).unlink(missing_ok=True)
    except OSError:
        log.exception("could not delete a removed receipt's photo")
        return False
    finally:
        await db.commit()
    return True


async def remove_purchase(db: AsyncSession, user: AppUser, purchase_id: uuid.UUID) -> Removed:
    purchase = await get_purchase(db, purchase_id, lock=True)
    if purchase.status == "voided":
        raise ApiError(409, "already_removed", "This purchase was already removed.")
    plan = await removal_plan(db, purchase, lock=True)
    if plan.blocked:
        raise still_reading()
    if plan.outcome == "void":
        for observation_id in plan.live:
            await pricebook.void(db, observation_id, VOID_REASON, user)
        purchase.status = "voided"
        purchase.voided_at = datetime.now(UTC)
        purchase.voided_by = user.id
        await db.commit()
        return Removed("void", False, await get_purchase(db, purchase_id))
    await _delete(db, purchase, plan.job)
    await db.commit()
    return Removed("delete", await delete_photo(db, plan.document))


async def restore_blocked(
    db: AsyncSession, purchase: Purchase, *, lock: bool = False
) -> Literal["read_again"] | None:
    """Why a voided purchase can't be restored, or None.

    Uploading a removed receipt again reads it into a new draft and takes its job
    away from the voided purchase (``ingest.upload_receipt``). Restoring the old
    one as well would put the same receipt in the price book twice.
    """
    if purchase.receipt_document_id is None:
        return None
    stmt = select(IngestJob).where(IngestJob.receipt_document_id == purchase.receipt_document_id)
    if lock:
        stmt = stmt.with_for_update()
    job = (await db.execute(stmt.execution_options(populate_existing=True))).scalar_one_or_none()
    if job is not None and job.purchase_id != purchase.id:
        return "read_again"
    return None


async def restore_purchase(db: AsyncSession, purchase_id: uuid.UUID) -> Purchase:
    """Bring a voided purchase back to reviewed, ready to commit again (#210)."""
    # The purchase before its job, the order every other change takes them in.
    purchase = await get_purchase(db, purchase_id, lock=True)
    if purchase.status != "voided":
        raise ApiError(409, "not_voided", "Only a removed purchase can be restored.")
    if await restore_blocked(db, purchase, lock=True) == "read_again":
        raise ApiError(
            409,
            "read_again",
            "Its receipt was uploaded again, so it already has a newer purchase.",
        )
    purchase.status = "reviewed"
    purchase.voided_at = None
    purchase.voided_by = None
    await db.commit()
    return await get_purchase(db, purchase_id)


async def remove_failed_job(db: AsyncSession, job_id: uuid.UUID) -> bool:
    """Discard a receipt that could not be read, with its draft if it has one.

    Returns whether its photo was deleted.
    """
    stmt = select(IngestJob).where(IngestJob.id == job_id)
    job = (await db.execute(stmt)).scalar_one_or_none()
    if job is None:
        raise ApiError(404, "not_found", "Ingest job not found.")
    # The purchase before the job, the order every other change takes them
    # in (removing a purchase, committing one), so two can never deadlock.
    purchase = (
        await get_purchase(db, job.purchase_id, lock=True) if job.purchase_id is not None else None
    )
    job = (
        await db.execute(stmt.with_for_update().execution_options(populate_existing=True))
    ).scalar_one()
    if purchase is not None and job.purchase_id != purchase.id:
        raise ApiError(409, "conflict", "This receipt changed meanwhile. Reload and try again.")
    if job.status == "discarded":
        raise ApiError(404, "receipt_removed", "This receipt was removed.")
    if job.status in READING:
        raise still_reading()
    if job.status != "failed":
        raise ApiError(409, "not_failed", "Only a receipt that couldn't be read is removed here.")
    if purchase is not None:
        plan = await removal_plan(db, purchase)
        if plan.outcome == "void":
            # A draft cannot have reached the price book; if this one did, it
            # is removed from its purchase page, where the outcome is shown.
            raise ApiError(
                409,
                "recorded",
                "Part of this receipt is in the price book. Remove it from its purchase page.",
            )
    document = await _photo_to_delete(
        db, job.receipt_document_id, purchase.id if purchase is not None else None
    )
    if purchase is not None:
        await _delete(db, purchase, job)
    else:
        _discard(job)
    await db.commit()
    return await delete_photo(db, document)
