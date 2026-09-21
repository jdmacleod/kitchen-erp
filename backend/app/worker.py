"""Ingest worker. Phase 1A ships the loop and its shutdown; stages arrive in Phase 2."""

from __future__ import annotations

import asyncio
import signal

from app.core.logging import get_logger

log = get_logger(__name__)


async def run(poll_seconds: float = 5.0) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    log.info("worker started", extra={"poll_seconds": poll_seconds})
    while not stop.is_set():
        # Phase 2: claim a job with SELECT ... FOR UPDATE SKIP LOCKED and run its stage.
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
        except TimeoutError:
            continue
    log.info("worker stopped")
