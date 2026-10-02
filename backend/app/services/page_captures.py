"""Captured vendor pages (04, 2M): the bookmarklet's clip and a pasted address.

```
POST /product-captures ─▶ vendor (chosen, or matched by the page's host) ─▶ listing key
                       ─▶ generic ladder now (structured data, meta, address)
                       ─▶ capture + proposal (+ the photos the browser could read)
                       ─▶ extract job: retailer adapters, then the model over the page text
```

Nothing here fetches anything: the page, its text and any images arrive from the
person's own browser, and image addresses are only kept (non-negotiable 9).
"""

from __future__ import annotations

import base64
import binascii
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import extract as ladder
from app.catalog import proposals as merging
from app.catalog.listings import canonical_url
from app.core.errors import ApiError
from app.core.ids import new_id
from app.core.logging import get_logger
from app.ingest.errors import IngestError, ModelUnavailable
from app.models import AppUser, ProductCapture, ProductJob, ProductProposal
from app.models.geo import Vendor
from app.services import plugins, product_photos, proposals
from app.services.product_photos import PhotoUpload

log = get_logger(__name__)

MAX_PAGE_TEXT = 200 * 1024
MAX_STRUCTURED = 200 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGES = 4
MODEL_TEXT_CHARS = 12_000
MAX_ATTEMPTS = 3


@dataclass
class PageCapture:
    page_url: str
    channel: str = "clip"
    canonical_url: str | None = None
    title: str | None = None
    meta: dict[str, str] = field(default_factory=dict)
    structured_data: list[str] = field(default_factory=list)
    dom_text: str = ""
    image_urls: list[str] = field(default_factory=list)
    images: list[tuple[str, str]] = field(default_factory=list)  # (address, base64)
    vendor_id: uuid.UUID | None = None
    without_store: bool = False


def check_sizes(data: PageCapture) -> list[bytes]:
    """Refuse a page that is too large, naming the field (criterion 84); decode its images."""
    if len(data.dom_text.encode()) > MAX_PAGE_TEXT:
        raise ApiError(
            413, "payload_too_large", "This page is too large to save.", {"field": "dom_text"}
        )
    if sum(len(b.encode()) for b in data.structured_data) > MAX_STRUCTURED:
        raise ApiError(
            413,
            "payload_too_large",
            "This page's product data is too large to save.",
            {"field": "structured_data"},
        )
    decoded = []
    for _, encoded in data.images[:MAX_IMAGES]:
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise ApiError(422, "validation_error", "An image is not base64.") from None
        if len(raw) > MAX_IMAGE_BYTES:
            raise ApiError(413, "payload_too_large", "An image is over 5 MB.", {"field": "images"})
        decoded.append(raw)
    return decoded


def _host(url: str | None) -> str | None:
    try:
        host = urlsplit(url or "").hostname
    except ValueError:
        return None
    return host.removeprefix("www.") if host else None


async def match_vendor(db: AsyncSession, page_url: str) -> Vendor | None:
    """The household's vendor whose website is on the page's host, if one is."""
    host = _host(page_url)
    if not host:
        return None
    for vendor in (await db.execute(select(Vendor).where(Vendor.website.is_not(None)))).scalars():
        site = _host(vendor.website if "//" in (vendor.website or "") else f"//{vendor.website}")
        if site and (host == site or host.endswith(f".{site}")):
            return vendor
    return None


@dataclass
class AddressPreview:
    vendor: Vendor | None
    canonical_url: str
    title: str | None
    item_number: str | None


async def preview(db: AsyncSession, page_url: str) -> AddressPreview:
    """What an address alone says: its vendor, title and item number. No request is made."""
    try:
        canonical, _ = canonical_url(page_url)
    except ValueError:
        raise ApiError(422, "invalid_url", "That is not a web address.") from None
    fields = merging.merge(ladder.from_address(page_url).candidates)
    return AddressPreview(
        vendor=await match_vendor(db, page_url),
        canonical_url=canonical,
        title=merging.value(fields, "title"),
        item_number=merging.value(fields, "item_number"),
    )


def _price(fields: dict[str, Any]) -> dict[str, Any] | None:
    amount = merging.value(fields, "price")
    if amount is None:
        return None
    return {
        "amount": str(amount),
        "qty": "1",
        "unit": "each",
        "is_promo": bool(merging.value(fields, "on_sale")),
    }


async def capture_page(
    db: AsyncSession, user: AppUser, data: PageCapture
) -> proposals.CaptureResult:
    images = check_sizes(data)
    try:
        canonical, store_ref = canonical_url(data.page_url, data.canonical_url)
    except ValueError:
        raise ApiError(422, "invalid_url", "That is not a web address.") from None
    vendor = None
    if data.vendor_id is not None:
        vendor = await db.get(Vendor, data.vendor_id)
        if vendor is None:
            raise ApiError(404, "not_found", "No such vendor.")
    elif not data.without_store:
        vendor = await match_vendor(db, data.page_url)
    evidence = ladder.extract(data.page_url, meta=data.meta, structured_data=data.structured_data)
    if data.title:
        evidence.candidates.append(merging.Candidate("title", data.title[:200], "page_meta"))
    fields = merging.merge(evidence.candidates)
    listing = None
    if vendor is not None:
        listing = {
            "vendor_id": str(vendor.id),
            "canonical_url": canonical,
            "title": merging.value(fields, "title") or data.title or canonical,
            "vendor_sku": merging.value(fields, "item_number"),
            "store_ref": store_ref,
        }
    payload = {
        "page_url": data.page_url,
        "canonical_url": data.canonical_url,
        "title": data.title,
        "meta": data.meta,
        "structured_data": data.structured_data,
        "image_urls": (data.image_urls + evidence.images)[: ladder.MAX_IMAGES],
        "dom_text": data.dom_text,
        "vendor_id": str(vendor.id) if vendor else None,
    }
    result = await proposals.create_capture(
        db,
        user=user,
        channel=data.channel,
        payload=payload,
        evidence=proposals.Evidence(
            candidates=evidence.candidates,
            listing=listing,
            # A page without a store keeps its product details, but no price (2M).
            price=_price(fields) if listing else None,
        ),
        source_url=data.page_url,
        commit=False,
    )
    if result.created:
        if images:
            await product_photos.add_photos(
                db,
                None,
                [PhotoUpload(raw) for raw in images],
                proposal_id=result.proposal.id,
                vendor_id=vendor.id if vendor else None,
                source_kind="vendor_listing",
                commit=False,
            )
        if data.dom_text.strip() or plugins.adapters():
            db.add(ProductJob(id=new_id(), kind="extract", product_capture_id=result.capture.id))
    await db.commit()
    await db.refresh(result.proposal)
    return result


# --- the extract job ---------------------------------------------------------------


async def run_job(db: AsyncSession, job: ProductJob) -> None:
    """Retailer adapters, then the model over the page text; each only adds candidates."""
    from app.services import identify

    started = time.monotonic()
    capture = await db.get(ProductCapture, job.product_capture_id)
    proposal = (
        await db.execute(
            select(ProductProposal).where(ProductProposal.capture_id == job.product_capture_id)
        )
    ).scalar_one_or_none()
    output: dict[str, Any] = {"path": "page"}
    if capture is None or proposal is None or proposal.status != "pending":
        output["skipped"] = "proposal decided"
        await product_photos.record(db, job, started, output)
        job.status = "done"
        await db.commit()
        return
    page = {k: capture.payload.get(k) for k in ("page_url", "canonical_url", "title", "meta")}
    page["structured_data"] = capture.payload.get("structured_data") or []
    page["dom_text"] = capture.payload.get("dom_text") or ""
    found, records = plugins.run(page)
    output["adapters"] = records
    text = page["dom_text"].strip()
    await db.commit()  # before any model call (F11-2)
    if text:
        try:
            found.extend(await identify.read_text(text[:MODEL_TEXT_CHARS]))
        except ModelUnavailable as exc:
            if job.attempts < MAX_ATTEMPTS:
                job.status = "pending"
                job.last_error = exc.code
                await product_photos.record(db, job, started, {**output, "error": exc.code})
                await db.commit()
                return
            output["error"] = exc.code
        except IngestError as exc:
            output["error"] = exc.code
    if found:
        proposal = await proposals.get_proposal(db, proposal.id, lock=True)
        if proposal.status == "pending":
            fields = merging.merge([*merging.candidates_of(proposal.fields), *found])
            changes: dict[str, Any] = {
                "fields": fields,
                "match": await proposals.match_catalog(db, fields, proposal.listing),
            }
            if proposal.listing:
                changes["price"] = _price(fields)
            await proposals.write_proposal(db, proposal, **changes)
        output["fields"] = sorted({c.field for c in found})
    await product_photos.record(db, job, started, output)
    job.status = "done"
    job.last_error = output.get("error")
    await db.commit()
