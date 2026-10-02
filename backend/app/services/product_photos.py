"""Product photos: upload, processing, the main photo, and the person's choices (03, 1I).

```
POST /product-photos ─▶ header probe (415 / 422) ─▶ incoming/ + product_image(processing)
                                                    + product_job(image_process)
worker ─▶ normalize ─▶ originals/ ─▶ mask? ─▶ masks/ ─▶ derived/ ─▶ active ─▶ reselect
```

The API never decodes pixels: it reads the header, stores the bytes and queues
a job. The worker decodes, normalizes and makes the derivatives, then chooses
the product's main photo with the product row locked, as every choice does, so
concurrent choices end with one main photo.
"""

from __future__ import annotations

import hashlib
import os
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import anyio
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.photos import PhotoFacts, eligible, select_primary
from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.ids import new_id
from app.core.logging import get_logger
from app.ingest.errors import StageFailure
from app.models import Product, ProductImage, ProductJob, ProductStageResult
from app.services import media
from app.services.ingest import sniff_mime

log = get_logger(__name__)

MAX_PHOTOS_PER_UPLOAD = 4
PHOTO_MAX_BYTES = 32 * 1024 * 1024
MAX_ATTEMPTS = 3
ADAPTER = "image_pipeline"


@dataclass(frozen=True)
class PhotoUpload:
    data: bytes
    role: str = "product"


# --- the main photo -------------------------------------------------------------


def facts(image: ProductImage) -> PhotoFacts:
    return PhotoFacts(
        id=image.id,
        source_kind=image.source_kind,
        role=image.role,
        status=image.status,
        created_at=image.created_at,
        width=image.width,
        height=image.height,
        has_cutout=image.mask_sha256 is not None,
        is_stock_suspect=image.is_stock_suspect,
        pinned=image.pinned,
        pinned_at=image.pinned_at,
    )


async def get_product(db: AsyncSession, product_id: uuid.UUID | None) -> Product:
    product = await db.get(Product, product_id, populate_existing=True) if product_id else None
    if product is None:
        raise ApiError(404, "not_found", "No such product.")
    return product


async def lock_product(db: AsyncSession, product_id: uuid.UUID) -> Product:
    product = (
        await db.execute(
            select(Product)
            .where(Product.id == product_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if product is None:
        raise ApiError(404, "not_found", "No such product.")
    return product


async def images_of(db: AsyncSession, product_id: uuid.UUID) -> list[ProductImage]:
    return list(
        (
            await db.execute(
                select(ProductImage)
                .where(ProductImage.product_id == product_id)
                .order_by(ProductImage.created_at, ProductImage.id)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )


async def reselect(db: AsyncSession, product: Product) -> uuid.UUID | None:
    """Choose the main photo again. The caller holds the product row's lock."""
    images = await images_of(db, product.id)
    product.primary_image_id = select_primary(facts(i) for i in images)
    return product.primary_image_id


async def reselect_all(db: AsyncSession) -> int:
    """`kerp images reselect`: every product with photos; returns how many changed."""
    product_ids = (
        (
            await db.execute(
                select(ProductImage.product_id)
                .distinct()
                .where(ProductImage.product_id.is_not(None))
            )
        )
        .scalars()
        .all()
    )
    changed = 0
    for product_id in product_ids:
        product = await lock_product(db, product_id)
        before = product.primary_image_id
        if await reselect(db, product) != before:
            changed += 1
        await db.commit()
    return changed


# --- upload ---------------------------------------------------------------------


def _refuse(exc: StageFailure) -> ApiError:
    if exc.code == "image_too_large":
        return ApiError(
            422, "image_too_large", "This photo is over 50 megapixels.", {"detail": exc.detail}
        )
    return ApiError(415, "unsupported_image", "That file is not a photo this can read.")


def _probe(data: bytes) -> tuple[str, tuple[int, int]]:
    """The photo's MIME type and displayed size, or the refusal the API answers with."""
    if not data:
        raise ApiError(422, "validation_error", "The photo is empty.")
    if len(data) > PHOTO_MAX_BYTES:
        raise ApiError(413, "payload_too_large", "The photo exceeds the size limit.")
    mime = sniff_mime(data)
    if mime not in media.PHOTO_MIMES:
        raise ApiError(415, "unsupported_image", "Photos must be JPEG, PNG, WebP or HEIC.")
    try:
        return mime, media.probe(data)
    except StageFailure as exc:
        raise _refuse(exc) from None


def check_uploads(uploads: list[PhotoUpload]) -> list[str]:
    """Each photo's MIME type, or the refusal; every file is checked before any is stored."""
    if not 1 <= len(uploads) <= MAX_PHOTOS_PER_UPLOAD:
        raise ApiError(422, "validation_error", "Add between one and four photos at a time.")
    return [_probe(u.data)[0] for u in uploads]


async def add_photos(
    db: AsyncSession,
    product_id: uuid.UUID | None,
    uploads: list[PhotoUpload],
    *,
    proposal_id: uuid.UUID | None = None,
    vendor_id: uuid.UUID | None = None,
    source_kind: str = "user_photo",
    captured_at: datetime | None = None,
    commit: bool = True,
) -> list[ProductImage]:
    """Store up to four photos for a product, or for a proposal, and queue them.

    A repeat upload to the same product or proposal returns the same image. A
    proposal's photos are candidates until it is accepted (2L).
    """
    if (product_id is None) == (proposal_id is None):
        raise ValueError("a photo belongs to a product or to a proposal")
    mimes = check_uploads(uploads)
    if product_id is not None and await db.get(Product, product_id) is None:
        raise ApiError(404, "not_found", "No such product.")
    owner = (
        ProductImage.product_id == product_id
        if product_id is not None
        else ProductImage.proposal_id == proposal_id
    )
    out: list[ProductImage] = []
    for mime, upload in zip(mimes, uploads, strict=True):
        digest = hashlib.sha256(upload.data).hexdigest()
        existing = (
            await db.execute(
                select(ProductImage).where(owner, ProductImage.upload_sha256 == digest)
            )
        ).scalar_one_or_none()
        if existing is not None:
            out.append(existing)
            continue
        path = media.incoming_path(digest, media.PHOTO_MIMES[mime])
        if not path.is_file():
            await anyio.to_thread.run_sync(media.write_atomic, path, upload.data)
        image = ProductImage(
            id=new_id(),
            product_id=product_id,
            proposal_id=proposal_id,
            vendor_id=vendor_id,
            upload_sha256=digest,
            source_kind=source_kind,
            role=upload.role,
            status="processing",
            captured_at=captured_at,
        )
        db.add(image)
        await db.flush()
        db.add(ProductJob(id=new_id(), kind="image_process", product_image_id=image.id))
        out.append(image)
    if not commit:
        await db.flush()
        return out
    await db.commit()
    for image in out:
        await db.refresh(image)
    return out


async def _lock_image(db: AsyncSession, image_id: uuid.UUID) -> ProductImage:
    return (
        await db.execute(
            select(ProductImage)
            .where(ProductImage.id == image_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def get_image(db: AsyncSession, image_id: uuid.UUID) -> ProductImage:
    image = await db.get(ProductImage, image_id, populate_existing=True)
    if image is None:
        raise ApiError(404, "not_found", "No such photo.")
    return image


async def add_mask(
    db: AsyncSession, image_id: uuid.UUID, data: bytes, *, source: str
) -> ProductImage:
    """Queue a mask for a processed photo; the worker stores it and makes the cutouts."""
    image = await get_image(db, image_id)
    if image.sha256 is None or image.width is None or image.height is None:
        raise ApiError(409, "photo_processing", "This photo is still being prepared.")
    if len(data) > PHOTO_MAX_BYTES:
        raise ApiError(413, "payload_too_large", "The mask exceeds the size limit.")
    try:
        await anyio.to_thread.run_sync(media.probe_mask, data, (image.width, image.height))
    except StageFailure as exc:
        if exc.code == "mask_mismatch":
            raise ApiError(
                422,
                "mask_mismatch",
                "The mask is not the size of this photo.",
                {"size": exc.detail},
            ) from None
        raise ApiError(
            415, "unsupported_image", "The mask is not an image this can read."
        ) from None
    await anyio.to_thread.run_sync(
        media.write_atomic, media.incoming_mask_path(image.id, source), data
    )
    db.add(ProductJob(id=new_id(), kind="image_process", product_image_id=image.id))
    await db.commit()
    return await get_image(db, image_id)


# --- the person's choices ---------------------------------------------------------


async def _choose(db: AsyncSession, image_id: uuid.UUID, action: str) -> ProductImage:
    image = await get_image(db, image_id)
    if image.product_id is None:
        raise ApiError(409, "not_a_product_photo", "This photo is waiting on a proposal.")
    product = await lock_product(db, image.product_id)
    image = await get_image(db, image_id)  # re-read under the lock
    if action == "use_as_main":
        if not eligible(facts(image)):
            raise ApiError(409, "not_eligible", "Only a shown product photo can be the main photo.")
        await db.execute(
            update(ProductImage)
            .where(ProductImage.product_id == product.id, ProductImage.pinned)
            .values(pinned=False, pinned_at=None)
        )
        image = await get_image(db, image_id)
        image.pinned = True
        image.pinned_at = datetime.now(UTC)
    elif action == "unset_main":
        image.pinned = False
        image.pinned_at = None
    elif action == "hide":
        if image.status != "active":
            raise ApiError(409, "not_shown", "Only a shown photo can be hidden.")
        image.status = "hidden"
        image.pinned = False
        image.pinned_at = None
    elif action == "show":
        if image.status != "hidden":
            raise ApiError(409, "not_hidden", "This photo is not hidden.")
        image.status = "active"
    await db.flush()
    await reselect(db, product)
    await db.commit()
    return await get_image(db, image_id)


async def use_as_main(db: AsyncSession, image_id: uuid.UUID) -> ProductImage:
    return await _choose(db, image_id, "use_as_main")


async def unset_main(db: AsyncSession, image_id: uuid.UUID) -> ProductImage:
    return await _choose(db, image_id, "unset_main")


async def hide(db: AsyncSession, image_id: uuid.UUID) -> ProductImage:
    return await _choose(db, image_id, "hide")


async def show(db: AsyncSession, image_id: uuid.UUID) -> ProductImage:
    return await _choose(db, image_id, "show")


async def retry(db: AsyncSession, image_id: uuid.UUID) -> ProductImage:
    image = await get_image(db, image_id)
    if image.status != "failed":
        raise ApiError(409, "not_failed", "Only a photo that failed can be retried.")
    image.status = "processing"
    db.add(ProductJob(id=new_id(), kind="image_process", product_image_id=image.id))
    await db.commit()
    return await get_image(db, image_id)


# --- the worker -------------------------------------------------------------------


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


async def claim_job(db: AsyncSession, locked_by: str) -> ProductJob | None:
    now = datetime.now(UTC)
    stale = now - timedelta(seconds=get_settings().ingest_lock_timeout_seconds)
    job = (
        await db.execute(
            select(ProductJob)
            .where(
                or_(
                    ProductJob.status == "pending",
                    (ProductJob.status == "running") & (ProductJob.locked_at < stale),
                )
            )
            .order_by(ProductJob.created_at, ProductJob.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if job is None:
        await db.rollback()
        return None
    job.status = "running"
    job.attempts += 1
    job.locked_at = now
    job.locked_by = locked_by
    await db.commit()
    return job


def _incoming_upload(image: ProductImage) -> Path | None:
    for ext in sorted(set(media.PHOTO_MIMES.values())):
        path = media.incoming_path(image.upload_sha256, ext)
        if path.is_file():
            return path
    return None


def _incoming_masks(image: ProductImage) -> list[tuple[str, Path]]:
    found = [(s, media.incoming_mask_path(image.id, s)) for s in ("device", "tool")]
    return [(s, p) for s, p in found if p.is_file()]


@dataclass
class _Processed:
    original: media.Normalized | None
    mask: media.NormalizedMask | None
    mask_source: str | None
    mask_discarded: bool
    used: list[Path]


def _process_files(image: ProductImage) -> _Processed:
    """The file work for one photo: normalize the upload, then any mask, then derivatives."""
    used: list[Path] = []
    original = None
    sha, size = image.sha256, (image.width, image.height)
    if sha is None:
        upload = _incoming_upload(image)
        if upload is None:
            raise StageFailure(code="upload_missing")
        original = media.normalize(upload.read_bytes())
        media.write_atomic(media.original_path(original.sha256, original.ext), original.data)
        used.append(upload)
        sha, size = original.sha256, (original.width, original.height)
    mask, mask_source, discarded = None, None, False
    # The newest mask wins when both are waiting.
    pending = sorted(_incoming_masks(image), key=lambda sp: sp[1].stat().st_mtime)
    if pending:
        mask_source, mask_file = pending[-1]
        mask = media.normalize_mask(mask_file.read_bytes(), size)  # type: ignore[arg-type]
        if mask is None:
            discarded = True
        else:
            media.write_atomic(media.mask_path(mask.sha256), mask.data)
        used.extend(p for _, p in pending)
    mask_sha = mask.sha256 if mask else (None if discarded else image.mask_sha256)
    media.build_all(sha, mask_sha)
    return _Processed(original, mask, mask_source, discarded, used)


async def record(
    db: AsyncSession, job: ProductJob, started: float, output: dict[str, object]
) -> None:
    db.add(
        ProductStageResult(
            id=new_id(),
            job_id=job.id,
            stage=job.kind,
            adapter=ADAPTER,
            adapter_version=media.pipeline_version(),
            output=output,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    )


async def run_job(db: AsyncSession, job: ProductJob) -> None:
    """Run one claimed job: prepare a photo, or identify a captured product (2L)."""
    if job.kind == "identify":
        from app.services import identify

        await identify.run_job(db, job)
        return
    started = time.monotonic()
    image = await get_image(db, job.product_image_id)  # type: ignore[arg-type]
    try:
        processed = await anyio.to_thread.run_sync(_process_files, image)
    except StageFailure as exc:
        await record(db, job, started, {"error": exc.code, "detail": exc.detail})
        job.status = "failed"
        job.last_error = exc.code
        if image.status == "processing":
            image.status = "failed"
        await db.commit()
        log.info("photo failed", extra={"image_id": str(image.id), "code": exc.code})
        return
    except OSError as exc:
        # The disk, not the photo: try again, up to a limit.
        await record(db, job, started, {"error": "io_error", "detail": type(exc).__name__})
        job.last_error = "io_error"
        job.status = "pending" if job.attempts < MAX_ATTEMPTS else "failed"
        if job.status == "failed" and image.status == "processing":
            image.status = "failed"
        await db.commit()
        return

    # Lock in accept's order, product then photo, so an accept that attaches this
    # photo to a product while it was being prepared is seen here (2L).
    product = await lock_product(db, image.product_id) if image.product_id else None
    image = await _lock_image(db, image.id)
    if product is None and image.product_id is not None:
        product = await lock_product(db, image.product_id)
    output: dict[str, object] = {}
    if processed.original is not None:
        o = processed.original
        image.sha256, image.width, image.height, image.phash = o.sha256, o.width, o.height, o.phash
        output.update(sha256=o.sha256, width=o.width, height=o.height)
    if processed.mask is not None:
        image.mask_sha256 = processed.mask.sha256
        image.cutout_source = processed.mask_source
        output.update(mask_sha256=processed.mask.sha256, mask_cover=round(processed.mask.cover, 4))
    elif processed.mask_discarded:
        image.mask_sha256 = None
        image.cutout_source = None
        output["mask"] = "discarded"
    if image.status in ("processing", "failed"):
        # A proposal's photo is a candidate until the proposal is accepted.
        image.status = "active" if image.product_id else "candidate"
    await db.flush()
    if product is not None:
        await reselect(db, product)
    await record(db, job, started, output)
    job.status = "done"
    job.last_error = None
    await db.commit()
    for path in processed.used:
        path.unlink(missing_ok=True)
    log.info("photo processed", extra={"image_id": str(image.id)})


async def run_once(db: AsyncSession, *, locked_by: str | None = None) -> bool:
    """Claim and run one product job. Returns False when none was waiting."""
    job = await claim_job(db, locked_by or worker_id())
    if job is None:
        return False
    await run_job(db, job)
    return True


async def rebuild_derivatives(db: AsyncSession) -> int:
    """`kerp images rebuild`: make every current derivative that is missing.

    Returns how many derivative files the photos have afterwards.
    """
    rows = (
        await db.execute(
            select(ProductImage.sha256, ProductImage.mask_sha256).where(
                ProductImage.sha256.is_not(None)
            )
        )
    ).all()
    made = 0
    for sha, mask_sha in rows:
        made += len(await anyio.to_thread.run_sync(media.build_all, sha, mask_sha))
    return made
