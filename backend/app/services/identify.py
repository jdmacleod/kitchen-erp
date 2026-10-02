"""Identifying a photographed product (04, 2L): the ``identify`` product job.

```
photos ─▶ a barcode in any of them? ─▶ treated as a barcode capture: the scanned
                                       GTIN, the USDA branded fields, a new match
       └▶ none ─▶ the model paths (S6): the vision model, else Tesseract → text model
```

A proposal is always made, even when nothing can be read; this job only adds
what it finds. Barcode reading is local (zxing-cpp); nothing leaves the machine.
"""

from __future__ import annotations

import time
from typing import Any

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import proposals as merging
from app.catalog.identifiers import InvalidGtin, classify_barcode
from app.core.logging import get_logger
from app.ingest.raster import PHOTO_MAX_MEGAPIXELS, guard_megapixels
from app.models import ProductJob, ProductProposal
from app.services import barcode_lookup, media, product_photos, proposals

log = get_logger(__name__)

# The retail barcodes a product carries; QR codes and the like say nothing about it.
RETAIL = ("EAN13", "EAN8", "UPCA", "UPCE")


def read_codes(data: bytes) -> list[str]:
    """GTIN-14s of the retail barcodes in a photo, in the order found; local and pure."""
    import zxingcpp
    from PIL import Image

    try:
        with media._open(data) as opened:
            guard_megapixels(
                opened.width, opened.height, code="image_too_large", limit=PHOTO_MAX_MEGAPIXELS
            )
            image = opened.convert("L")
    except (Image.DecompressionBombError, OSError, ValueError, SyntaxError):
        return []
    formats = [getattr(zxingcpp.BarcodeFormat, name) for name in RETAIL]
    out: list[str] = []
    for found in zxingcpp.read_barcodes(image, formats=formats):
        try:
            scheme, value = classify_barcode(found.text)
        except (InvalidGtin, ValueError):
            continue
        if scheme == "gtin" and value not in out:
            out.append(value)
    return out


def _photo_bytes(image) -> bytes | None:
    if image.sha256 is not None and (original := media.find_original(image.sha256)):
        return original.read_bytes()
    upload = product_photos._incoming_upload(image)
    return upload.read_bytes() if upload else None


def _read_all(images: list) -> list[str]:
    codes: list[str] = []
    for image in images:
        data = _photo_bytes(image)
        for code in read_codes(data) if data else []:
            if code not in codes:
                codes.append(code)
    return codes


async def run_job(db: AsyncSession, job: ProductJob) -> None:
    started = time.monotonic()
    proposal = (
        await db.execute(
            select(ProductProposal).where(ProductProposal.capture_id == job.product_capture_id)
        )
    ).scalar_one_or_none()
    output: dict[str, Any] = {}
    if proposal is None or proposal.status != "pending":
        output["skipped"] = "proposal decided" if proposal else "no proposal"
    else:
        images = await proposals.photos_of(db, proposal.id)
        codes = await anyio.to_thread.run_sync(_read_all, images)
        if codes:
            output.update(path="barcode", codes=codes)
            await _as_barcode(db, proposal, codes[0])
        else:
            # The model paths arrive with S6; until then the proposal waits for a person.
            output["path"] = "unread"
    await product_photos.record(db, job, started, output)
    job.status = "done"
    job.last_error = None
    await db.commit()
    log.info("product identified", extra={"job_id": str(job.id), "path": output.get("path")})


async def _as_barcode(db: AsyncSession, proposal: ProductProposal, gtin: str) -> None:
    """A barcode in a photo is a scan: its GTIN and USDA fields join the proposal."""
    found = [
        merging.Candidate("gtin", gtin, "scan"),
        *await barcode_lookup.usda_candidates(db, gtin),
    ]
    fields = merging.merge([*merging.candidates_of(proposal.fields), *found])
    match = await proposals.match_catalog(db, fields, proposal.listing)
    await proposals.write_proposal(db, proposal, fields=fields, match=match)
