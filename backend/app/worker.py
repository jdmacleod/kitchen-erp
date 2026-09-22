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

log = get_logger(__name__)


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
        .order_by(IngestJob.next_attempt_at.nulls_first(), IngestJob.created_at)
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


async def run(poll_seconds: float = 5.0) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    me = worker_id()
    log.info("worker started", extra={"poll_seconds": poll_seconds, "worker": me})
    sessionmaker = get_sessionmaker()
    while not stop.is_set():
        try:
            async with sessionmaker() as db:
                ran = await run_once(db, locked_by=me)
        except Exception as exc:  # the database itself is unreachable, most likely
            log.error("worker loop error", extra={"exc_type": type(exc).__name__})
            ran = False
        if ran:
            continue
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
        except TimeoutError:
            continue
    log.info("worker stopped")
