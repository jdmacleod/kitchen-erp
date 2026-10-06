"""Ingest worker: claim one job at a time and run its current stage.

Claiming uses ``SELECT ... FOR UPDATE SKIP LOCKED`` on ``ingest_job`` so several
workers never collide and no broker is needed. A job is claimable when it is
``pending`` with no ``next_attempt_at`` or one in the past, or when it has been
``running`` longer than ``INGEST_LOCK_TIMEOUT_SECONDS`` (an abandoned lock).
The stage itself runs in :func:`app.ingest.stages.run_stage`.
"""

from __future__ import annotations

import asyncio
import os
import signal
import socket
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.logging import get_logger
from app.ingest.stages import RUNNABLE_STAGES, run_stage
from app.models import IngestJob
from app.services import lookups, naming, product_jobs, resolution

log = get_logger(__name__)

# How often an idle worker checks for listings due a refresh (the interval itself
# is LISTING_REFRESH_DAYS).
LISTING_REFRESH_CHECK_SECONDS = 3600


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


async def claim_job(db: AsyncSession, locked_by: str) -> IngestJob | None:
    """Lock and mark one claimable job as running; None when the queue is empty."""
    now = datetime.now(UTC)
    stale = now - timedelta(seconds=get_settings().ingest_lock_timeout_seconds)
    stmt = (
        select(IngestJob)
        .where(
            IngestJob.stage.in_(RUNNABLE_STAGES),
            or_(
                (IngestJob.status == "pending")
                & or_(IngestJob.next_attempt_at.is_(None), IngestJob.next_attempt_at <= now),
                (IngestJob.status == "running") & (IngestJob.locked_at < stale),
            ),
        )
        # A receipt already part-read is finished before a new one is started:
        # oldest-first alone left a read whose slow stages were done waiting
        # behind every older upload for a stage that takes milliseconds.
        .order_by(
            (IngestJob.stage == "captured").asc(),
            IngestJob.next_attempt_at.nulls_first(),
            IngestJob.created_at,
        )
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = (await db.execute(stmt)).scalar_one_or_none()
    if job is None:
        await db.rollback()
        return None
    job.status = "running"
    job.locked_at = now
    job.locked_by = locked_by
    await db.commit()
    return job


async def run_once(db: AsyncSession, *, locked_by: str | None = None, **kwargs: Any) -> bool:
    """Claim and run one stage of one job. Returns False when nothing was claimable."""
    job = await claim_job(db, locked_by or worker_id())
    if job is None:
        return False
    await run_stage(db, job, **kwargs)
    return True


def loop_error_fields(exc: BaseException) -> dict[str, str]:
    """What to log when a loop iteration fails, with a next step when one is known.

    Compose starts the worker alongside the API, so on a fresh install it polls
    before anyone has run `kerp migrate` and every poll fails on a missing table.
    A bare "ProgrammingError" every five seconds reads like a broken install.
    """
    fields = {"exc_type": type(exc).__name__}
    seen: BaseException | None = exc
    while seen is not None:
        for candidate in (seen, getattr(seen, "orig", None)):
            if getattr(candidate, "sqlstate", None) == "42P01":  # undefined_table
                fields["hint"] = "the database has no schema yet; run `kerp migrate`"
                return fields
        seen = seen.__cause__
    return fields


def prepare() -> bool:
    """What the worker sets up before its loop. Receipts are resolved here, not in
    the api, so the model ranker is installed here too; True when it was."""
    return resolution.install_default_ranker()


async def run(poll_seconds: float = 5.0) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    me = worker_id()
    ranker = prepare()
    log.info("worker started", extra={"poll_seconds": poll_seconds, "worker": me, "ranker": ranker})
    sessionmaker = get_sessionmaker()
    last_refresh = float("-inf")
    while not stop.is_set():
        try:
            async with sessionmaker() as db:
                ran = await run_once(db, locked_by=me)
                # Receipts first, then product work (1I), then one line for the
                # naming pass (#88).
                if not ran:
                    ran = await product_jobs.run_once(db, locked_by=me)
                if not ran:
                    ran = await naming.run_suggestion_once(db)
        except Exception as exc:  # the database itself is unreachable, most likely
            log.error("worker loop error", extra=loop_error_fields(exc))
            ran = False
        if ran:
            continue
        # Idle: at most hourly, queue listings due a refresh for the products helper.
        if loop.time() - last_refresh >= LISTING_REFRESH_CHECK_SECONDS:
            last_refresh = loop.time()
            try:
                async with sessionmaker() as db:
                    queued = await lookups.queue_listing_refreshes(db)
                if queued:
                    log.info("listing refreshes queued", extra={"count": queued})
            except Exception as exc:
                log.error("listing refresh error", extra=loop_error_fields(exc))
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
        except TimeoutError:
            continue
    log.info("worker stopped")
