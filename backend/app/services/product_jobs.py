"""The product work queue's dispatcher (03, 1I; 04, 2L): claim one job, run it by kind.

```
product_job ─▶ image_process ─▶ product_photos.run_job
            ─▶ identify      ─▶ identify.run_job
            ─▶ extract       ─▶ page_captures.run_job
a prepared household photo without a mask ─▶ lookups.queue_cutout (2N)
```

It sits above the modules that do the work, so none of them imports another to
find its jobs: the photo pipeline knows nothing of identification.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ProductImage, ProductJob
from app.services import identify, lookups, page_captures, product_photos

Runner = Callable[[AsyncSession, ProductJob], Awaitable[None]]

RUNNERS: dict[str, Runner] = {
    "image_process": product_photos.run_job,
    "identify": identify.run_job,
    "extract": page_captures.run_job,
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
    if job.kind == "image_process" and job.status == "done" and job.product_image_id:
        # A household photo without a mask: the products helper may make one (2N).
        image = await db.get(ProductImage, job.product_image_id)
        if image is not None:
            await lookups.queue_cutout(db, image)
            await db.commit()


async def run_once(db: AsyncSession, *, locked_by: str | None = None) -> bool:
    """Claim and run one product job. Returns False when none was waiting."""
    job = await product_photos.claim_job(db, locked_by or product_photos.worker_id())
    if job is None:
        return False
    await run_job(db, job)
    return True
