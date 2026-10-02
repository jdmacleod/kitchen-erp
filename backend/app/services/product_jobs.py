"""The product work queue's dispatcher (03, 1I; 04, 2L): claim one job, run it by kind.

```
product_job ─▶ image_process ─▶ product_photos.run_job
            ─▶ identify      ─▶ identify.run_job
```

It sits above the modules that do the work, so none of them imports another to
find its jobs: the photo pipeline knows nothing of identification.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ProductJob
from app.services import identify, product_photos

Runner = Callable[[AsyncSession, ProductJob], Awaitable[None]]

RUNNERS: dict[str, Runner] = {
    "image_process": product_photos.run_job,
    "identify": identify.run_job,
}


async def run_job(db: AsyncSession, job: ProductJob) -> None:
    runner = RUNNERS.get(job.kind)
    if runner is None:
        # A kind this build cannot run: leave it failed and say so, never loop on it.
        job.status = "failed"
        job.last_error = "unknown_kind"
        await db.commit()
        return
    await runner(db, job)


async def run_once(db: AsyncSession, *, locked_by: str | None = None) -> bool:
    """Claim and run one product job. Returns False when none was waiting."""
    job = await product_photos.claim_job(db, locked_by or product_photos.worker_id())
    if job is None:
        return False
    await run_job(db, job)
    return True
