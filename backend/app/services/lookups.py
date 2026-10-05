"""The products helper contract (04, 2N): what the app hands out, and what comes back.

```
person: "Look this up online"  ─┐
unknown scan (if turned on)     ├─▶ lookup_request (open) ─▶ helper reads (products:read)
photo processed without a mask ─┘                          ─▶ helper answers (products:suggest)
answer ─▶ validate (kitchen-erp-products/1) ─▶ refused? recorded, 422
       ─▶ pending proposal: merged, "via helper"
       ─▶ accepted proposal or a product: a Product update proposal, if it changes anything
       ─▶ rejected or superseded proposal: recorded and closed
listing prices ─▶ listing_price_change (pending) ─▶ a person accepts each ─▶ posted price
```

This application makes none of the helper's calls (non-negotiable 9). Every
answer is recorded in ``lookup_answer``, whatever became of it.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import anyio
from pydantic import ValidationError
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import proposals as merging
from app.catalog.identifiers import classify_barcode
from app.catalog.listings import canonical_url
from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.ids import new_id
from app.models import (
    ApiToken,
    AppUser,
    ListingPriceChange,
    LookupAnswer,
    LookupRequest,
    Product,
    ProductCapture,
    ProductImage,
    ProductProposal,
    VendorListing,
)
from app.models.geo import VendorLocation
from app.schemas.products_interchange import HelperAnswer, ListingPriceReport, PriceValue
from app.services import media, pricebook, product_photos, proposals, vendor_pages

HELPER_SCOPES = ("products:read", "products:suggest")


def parse_body(raw: bytes) -> Any:
    """A helper's JSON, with every number a Decimal: no float on the way in."""
    try:
        return json.loads(raw or b"null", parse_float=Decimal)
    except (ValueError, RecursionError):
        raise ApiError(422, "validation_error", "The body is not JSON.") from None


async def helper_configured(db: AsyncSession) -> bool:
    """Whether a products helper could be listening: an unrevoked token with its scopes."""
    rows = (
        await db.execute(select(ApiToken.scopes).where(ApiToken.revoked_at.is_(None)))
    ).scalars()
    return any(set(scopes) & set(HELPER_SCOPES) for scopes in rows)


# --- the queue ------------------------------------------------------------------------


async def _open(db: AsyncSession, **owner: Any) -> LookupRequest | None:
    clauses = [LookupRequest.status == "open"]
    clauses.extend(getattr(LookupRequest, k) == v for k, v in owner.items())
    return (await db.execute(select(LookupRequest).where(*clauses).limit(1))).scalar_one_or_none()


async def ask_for_proposal(
    db: AsyncSession, user: AppUser | None, proposal_id: uuid.UUID
) -> LookupRequest:
    """ "Look this up online" for a proposal: its barcode, else its page."""
    proposal = await proposals.get_proposal(db, proposal_id)
    if proposal.status != "pending":
        raise ApiError(409, "proposal_not_pending", "This proposal has already been decided.")
    gtin = merging.value(proposal.fields, "gtin")
    capture = await db.get(ProductCapture, proposal.capture_id) if proposal.capture_id else None
    page = (proposal.listing or {}).get("canonical_url") or (
        capture.source_url if capture else None
    )
    kind, value = ("gtin", gtin) if gtin else ("page", page)
    if not value:
        raise ApiError(409, "nothing_to_look_up", "There is no barcode or page to look up.")
    existing = await _open(db, proposal_id=proposal.id, kind=kind)
    if existing is not None:
        return existing
    request = LookupRequest(
        id=new_id(),
        kind=kind,
        proposal_id=proposal.id,
        value=str(value),
        requested_by=user.id if user else None,
    )
    db.add(request)
    await db.commit()
    return request


async def ask_for_product_page(
    db: AsyncSession, user: AppUser, product_id: uuid.UUID, page_url: str
) -> LookupRequest:
    """A page pasted in Add product, for the product it created (2M with a helper).

    The helper reads it; its answer opens a Product update, with the page's listing and
    posted price when the page is one of the household's vendors'."""
    if not await helper_configured(db):
        raise ApiError(
            409, "no_helper", "No lookup helper is set up, so this page can't be read from here."
        )
    if await db.get(Product, product_id) is None:
        raise ApiError(404, "not_found", "No such product.")
    try:
        page_url, _ = canonical_url(page_url)
    except ValueError:
        raise ApiError(422, "invalid_url", "That is not a web address.") from None
    existing = await _open(db, product_id=product_id, kind="page", value=page_url)
    if existing is not None:
        return existing
    request = LookupRequest(
        id=new_id(), kind="page", product_id=product_id, value=page_url, requested_by=user.id
    )
    db.add(request)
    await db.commit()
    return request


async def queue_cutout(db: AsyncSession, image: ProductImage) -> None:
    """A household photo with no mask: the helper may make one. The photo never leaves
    the machine unless the helper, holding products:read, reads it for this request."""
    if image.source_kind != "user_photo" or image.role != "product" or image.mask_sha256:
        return
    if image.product_id is None or await _open(db, product_image_id=image.id) is not None:
        return
    db.add(LookupRequest(id=new_id(), kind="cutout", product_image_id=image.id))


MAX_IMAGE_LOOKUPS = 4


async def queue_page_images(
    db: AsyncSession, proposal: ProductProposal, image_urls: list[str]
) -> int:
    """A clip whose images the browser could not read (another host refused it): the
    helper may fetch up to four of them. Only addresses the page itself showed are
    sent, and only to their own hosts. Not committed here."""
    if not image_urls or not await helper_configured(db):
        return 0
    queued = 0
    for url in image_urls[:MAX_IMAGE_LOOKUPS]:
        if not url.startswith(("https://", "http://")):
            continue
        if await _open(db, proposal_id=proposal.id, kind="image", value=url) is None:
            db.add(LookupRequest(id=new_id(), kind="image", proposal_id=proposal.id, value=url))
            queued += 1
    return queued


async def queue_unknown_scan(db: AsyncSession, proposal: ProductProposal, gtin: str) -> None:
    """An unknown scanned barcode, only when the household turned that on (off by default)."""
    if not get_settings().products_autoqueue_gtins:
        return
    if await _open(db, proposal_id=proposal.id, kind="gtin") is None:
        db.add(LookupRequest(id=new_id(), kind="gtin", proposal_id=proposal.id, value=gtin))


async def queue_listing_refreshes(db: AsyncSession, now: datetime | None = None) -> int:
    """Queue a ``page`` request for each active listing due a refresh (2N, 2026-10-02).

    Only while a products helper token exists, and at most once per listing every
    ``LISTING_REFRESH_DAYS``; an open request for a listing is never doubled.
    """
    days = get_settings().listing_refresh_days
    if days <= 0 or not await helper_configured(db):
        return 0
    now = now or datetime.now(UTC)
    due = (
        await db.execute(
            text(
                """
                SELECT l.id, l.product_id, l.canonical_url FROM vendor_listing l
                WHERE l.status = 'active' AND l.product_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM lookup_request r
                      WHERE r.listing_id = l.id
                        AND (r.status = 'open'
                             OR r.created_at
                                > CAST(:now AS timestamptz) - make_interval(days => :days))
                  )
                ORDER BY l.last_captured_at
                """
            ),
            {"now": now, "days": days},
        )
    ).all()
    for listing_id, product_id, url in due:
        db.add(
            LookupRequest(
                id=new_id(), kind="page", product_id=product_id, listing_id=listing_id, value=url
            )
        )
    await db.commit()
    return len(due)


async def open_requests(db: AsyncSession, limit: int = 100) -> list[LookupRequest]:
    return list(
        (
            await db.execute(
                select(LookupRequest)
                .where(LookupRequest.status == "open")
                .order_by(LookupRequest.created_at, LookupRequest.id)
                .limit(limit)
            )
        ).scalars()
    )


async def get_request(db: AsyncSession, request_id: uuid.UUID, *, lock: bool = False):
    stmt = select(LookupRequest).where(LookupRequest.id == request_id)
    if lock:
        stmt = stmt.with_for_update()
    request = (await db.execute(stmt)).scalar_one_or_none()
    if request is None:
        raise ApiError(404, "not_found", "No such lookup request.")
    return request


async def cutout_original(db: AsyncSession, request_id: uuid.UUID) -> Path:
    """A photo's original, readable only through an open cutout request for it (criterion 92)."""
    request = await get_request(db, request_id)
    if request.kind != "cutout" or request.status != "open":
        raise ApiError(404, "not_found", "No open cutout request with that id.")
    image = await db.get(ProductImage, request.product_image_id)
    path = media.find_original(image.sha256) if image and image.sha256 else None
    if path is None:
        raise ApiError(404, "not_found", "That photo has no original yet.")
    return path


async def answer_cutout(
    db: AsyncSession, token_id: uuid.UUID | None, request_id: uuid.UUID, mask: bytes
) -> ProductImage:
    request = await get_request(db, request_id, lock=True)
    if request.kind != "cutout" or request.status != "open":
        raise ApiError(409, "request_not_open", "That cutout request is not open.")
    image = await product_photos.add_mask(db, request.product_image_id, mask, source="tool")  # type: ignore[arg-type]
    request = await get_request(db, request_id, lock=True)
    request.status, request.answered_at = "answered", datetime.now(UTC)
    db.add(
        LookupAnswer(
            id=new_id(),
            request_id=request.id,
            token_id=token_id,
            body={"mask": True},
            outcome="merged",
        )
    )
    await db.commit()
    return image


# --- answers ---------------------------------------------------------------------------


def _refused(detail: str) -> ApiError:
    return ApiError(422, "answer_refused", "The answer was refused.", {"reason": detail[:500]})


async def _record(
    db: AsyncSession,
    request_id: uuid.UUID | None,
    token_id: uuid.UUID | None,
    body: Any,
    outcome: str,
    detail: str | None = None,
) -> None:
    db.add(
        LookupAnswer(
            id=new_id(),
            request_id=request_id,
            token_id=token_id,
            body=json.loads(json.dumps(body, default=str)),
            outcome=outcome,
            detail=detail,
        )
    )
    await db.commit()


def _candidates(answer: HelperAnswer) -> list[merging.Candidate]:
    out = []
    for c in answer.candidates:
        value: Any = c.value
        if isinstance(value, PriceValue):
            value = value.model_dump()
        if isinstance(value, Decimal):
            value = format(value, "f")
        elif isinstance(value, dict):
            value = {k: format(v, "f") if isinstance(v, Decimal) else v for k, v in value.items()}
        if c.field == "gtin":
            try:
                scheme, value = classify_barcode(str(value))
            except ValueError:
                raise ValueError("gtin: not a valid barcode") from None
            if scheme != "gtin":
                raise ValueError("gtin: not a barcode number")
        candidate = merging.Candidate(c.field, value, c.source, c.confidence, "helper")
        merging._check(candidate)  # unknown fields, wrong sources, over-cap confidence
        out.append(candidate)
    return out


def _photos(answer: HelperAnswer) -> list[tuple[bytes, bytes | None, Any]]:
    out = []
    for photo in answer.photos:
        try:
            data = base64.b64decode(photo.data_base64, validate=True)
            mask = base64.b64decode(photo.mask_base64, validate=True) if photo.mask_base64 else None
        except (binascii.Error, ValueError):
            raise ValueError("photos: not base64") from None
        out.append((data, mask, photo))
    return out


async def _has_listing(db: AsyncSession, product_id: uuid.UUID, listing: dict[str, Any]) -> bool:
    found = await db.execute(
        select(VendorListing.id).where(
            VendorListing.product_id == product_id,
            VendorListing.vendor_id == uuid.UUID(listing["vendor_id"]),
            VendorListing.canonical_url == listing["canonical_url"],
        )
    )
    return found.first() is not None


def _changes_product(product: Product, candidates: list[merging.Candidate]) -> bool:
    """Whether an answer would change a product the household already has."""
    current = {
        "title": product.name,
        "brand": product.brand,
        "pack": (
            {"qty": format(product.pack_qty, "f"), "unit": product.pack_unit}
            if product.pack_qty is not None
            else None
        ),
        "gtin": product.barcode_identifier.value if product.barcode_identifier else None,
    }
    for c in candidates:
        if c.field in current and current[c.field] in (None, ""):
            return True
        if c.field in current and current[c.field] != c.value and c.field != "title":
            return True
    return False


async def _store_photos(
    db: AsyncSession, proposal: ProductProposal, photos: list[tuple[bytes, bytes | None, Any]]
) -> None:
    if not photos:
        return
    images = await product_photos.add_photos(
        db,
        None,
        [product_photos.PhotoUpload(data, p.role) for data, _, p in photos],
        proposal_id=proposal.id,
        source_kind=photos[0][2].source_kind,
        commit=False,
    )
    for image, (_, mask, photo) in zip(images, photos, strict=True):
        image.source_kind = photo.source_kind
        image.attribution = photo.attribution
        image.source_url = photo.source_url
        if mask:
            # Applied by the photo's own job after it is prepared (1I, criterion 102).
            await anyio.to_thread.run_sync(
                media.write_atomic, media.incoming_mask_path(image.id, "tool"), mask
            )


async def answer(
    db: AsyncSession, token_id: uuid.UUID | None, body: Any
) -> tuple[str, uuid.UUID | None]:
    """Take one answer: merged, a Product update opened, nothing changed, or closed."""
    try:
        parsed = HelperAnswer.model_validate(body)
        candidates = _candidates(parsed)
        photos = _photos(parsed)
    except (ValidationError, ValueError, merging.UnknownCandidate) as exc:
        request_id = None
        if isinstance(body, dict):
            try:
                request_id = uuid.UUID(str(body.get("request_id")))
            except ValueError:
                request_id = None
            if request_id is not None and await db.get(LookupRequest, request_id) is None:
                request_id = None
        await _record(db, request_id, token_id, body, "refused", str(exc))
        raise _refused(str(exc)) from None

    request = await get_request(db, parsed.request_id, lock=True)
    if request.kind == "cutout":
        await _record(db, request.id, token_id, body, "refused", "cutouts are answered with a mask")
        raise _refused("cutouts are answered with a mask")
    now = datetime.now(UTC)
    request.status, request.answered_at = "answered", now

    proposal = await db.get(ProductProposal, request.proposal_id) if request.proposal_id else None
    product_id = request.product_id or (proposal.product_id if proposal else None)
    if proposal is not None and proposal.status == "pending":
        locked = await proposals.get_proposal(db, proposal.id, lock=True)
        fields = merging.merge([*merging.candidates_of(locked.fields), *candidates])
        match = await proposals.match_catalog(db, fields, locked.listing)
        await proposals.write_proposal(db, locked, fields=fields, match=match)
        await _store_photos(db, locked, photos)
        await _record(db, request.id, token_id, body, "merged")
        return "merged", locked.id
    if proposal is not None and proposal.status in ("rejected", "superseded"):
        await _record(db, request.id, token_id, body, "closed", f"proposal {proposal.status}")
        return "closed", proposal.id
    product = await db.get(Product, product_id) if product_id else None
    if product is None or not (candidates or photos) or not parsed.found:
        await _record(db, request.id, token_id, body, "no_change")
        return "no_change", None
    fields = merging.merge(candidates)
    # A pasted page (not a scheduled refresh) brings its listing and posted price along.
    listing = None
    if request.kind == "page" and request.listing_id is None and request.value:
        listing = await vendor_pages.listing_for_page(db, request.value, fields)
        if listing is not None and await _has_listing(db, product.id, listing):
            listing = None
    if not _changes_product(product, candidates) and not photos and listing is None:
        await _record(db, request.id, token_id, body, "no_change")
        return "no_change", None
    # A late answer about a product the household already has (PR7).
    update = ProductProposal(
        id=new_id(),
        capture_id=None,
        kind="product_update",
        product_id=product.id,
        status="pending",
        fields={},
        match={
            "strong": {"product_id": str(product.id), "reason": "identifier"},
            "candidates": [],
            "preselect": f"update:{product.id}",
        },
    )
    price = None
    if listing is not None:
        price = vendor_pages.posted_price(fields)
    # Through the one writer: a pending proposal for the same page is superseded.
    await proposals.write_proposal(db, update, fields=fields, listing=listing, price=price)
    await _store_photos(db, update, photos)
    await _record(db, request.id, token_id, body, "update_opened")
    return "update_opened", update.id


# --- posted prices the helper saw change (PR2) ----------------------------------------


async def report_prices(db: AsyncSession, token_id: uuid.UUID | None, body: Any) -> int:
    try:
        report = ListingPriceReport.model_validate(body)
    except ValidationError as exc:
        await _record(db, None, token_id, body, "refused", str(exc))
        raise _refused(str(exc)) from None
    added = 0
    for price in report.prices:
        listing = await db.get(VendorListing, price.listing_id)
        if listing is None or listing.product_id is None:
            continue
        if await _already_known(db, listing.id, price):
            continue
        db.add(
            ListingPriceChange(
                id=new_id(),
                listing_id=listing.id,
                amount=price.amount,
                qty=price.qty,
                unit=price.unit,
                is_promo=price.is_promo,
                seen_at=price.seen_at,
            )
        )
        added += 1
    await _record(db, None, token_id, {"prices": added}, "merged")
    return added


async def _already_known(db: AsyncSession, listing_id: uuid.UUID, price: Any) -> bool:
    """Whether a reported price equals the listing's latest posted price or its latest
    reported change (pending, accepted or rejected): only a change reaches a person."""
    rows = await db.execute(
        text(
            """
            (SELECT o.price AS amount, o.qty, o.unit, o.is_promo
             FROM price_observation o
             WHERE o.listing_id = :listing
               AND NOT EXISTS (SELECT 1 FROM price_observation_void v
                               WHERE v.observation_id = o.id)
             ORDER BY o.observed_at DESC, o.created_at DESC LIMIT 1)
            UNION ALL
            (SELECT c.amount, c.qty, c.unit, c.is_promo
             FROM listing_price_change c
             WHERE c.listing_id = :listing
             ORDER BY c.created_at DESC, c.id DESC LIMIT 1)
            """
        ),
        {"listing": listing_id},
    )
    reported = (price.amount, price.qty, price.unit, price.is_promo)
    return any((r.amount, r.qty, r.unit, r.is_promo) == reported for r in rows)


async def pending_price_changes(db: AsyncSession) -> list[dict[str, Any]]:
    rows = await db.execute(
        text(
            """
            SELECT c.id, c.amount, c.qty, c.unit, c.is_promo, c.seen_at, c.created_at,
                   l.id AS listing_id, l.title, l.canonical_url,
                   p.id AS product_id, p.name AS product_name,
                   v.id AS vendor_id, v.name AS vendor_name
            FROM listing_price_change c
            JOIN vendor_listing l ON l.id = c.listing_id
            JOIN product p ON p.id = l.product_id
            JOIN vendor v ON v.id = l.vendor_id
            WHERE c.status = 'pending'
            ORDER BY c.created_at, c.id
            """
        )
    )
    return [dict(r) for r in rows.mappings()]


async def _suggested_location(db: AsyncSession, vendor_id: uuid.UUID) -> uuid.UUID | None:
    return (
        await db.execute(
            text(
                """
                SELECT p.vendor_location_id FROM purchase p
                JOIN vendor_location vl ON vl.id = p.vendor_location_id
                WHERE vl.vendor_id = :vendor AND vl.active AND p.status = 'committed'
                ORDER BY p.purchased_at DESC LIMIT 1
                """
            ),
            {"vendor": vendor_id},
        )
    ).scalar_one_or_none()


async def decide_price_change(
    db: AsyncSession,
    user: AppUser,
    change_id: uuid.UUID,
    *,
    accept: bool,
    vendor_location_id: uuid.UUID | None = None,
) -> ListingPriceChange:
    change = (
        await db.execute(
            select(ListingPriceChange).where(ListingPriceChange.id == change_id).with_for_update()
        )
    ).scalar_one_or_none()
    if change is None:
        raise ApiError(404, "not_found", "No such price change.")
    if change.status != "pending":
        raise ApiError(409, "already_decided", "This price change was already decided.")
    change.decided_at, change.decided_by = datetime.now(UTC), user.id
    if not accept:
        change.status = "rejected"
        await db.commit()
        return change
    listing = await db.get(VendorListing, change.listing_id)
    assert listing is not None and listing.product_id is not None
    location_id = vendor_location_id or await _suggested_location(db, listing.vendor_id)
    location = await db.get(VendorLocation, location_id) if location_id else None
    if location is None or location.vendor_id != listing.vendor_id:
        raise ApiError(
            422, "location_required", "Say which of this vendor's stores the price is for."
        )
    observation = await pricebook.observe(
        db,
        product_id=listing.product_id,
        vendor_location_id=location.id,
        price=change.amount,
        qty=change.qty,
        unit=change.unit,
        is_promo=change.is_promo,
        source="listing",
        listing_id=listing.id,
        entered_by=user,
        observed_at=change.seen_at,
    )
    change.status, change.observation_id = "accepted", observation.id
    await db.commit()
    return change


# --- for the inbox and the reading line -------------------------------------------------


async def counts(db: AsyncSession) -> dict[str, Any]:
    pending_prices = (
        await db.execute(
            select(func.count())
            .select_from(ListingPriceChange)
            .where(ListingPriceChange.status == "pending")
        )
    ).scalar_one()
    overdue = (
        await db.execute(
            select(func.count(), func.min(LookupRequest.created_at)).where(
                LookupRequest.status == "open",
                or_(LookupRequest.kind == "gtin", LookupRequest.kind == "page"),
            )
        )
    ).one()
    return {"price_changes": int(pending_prices), "waiting": int(overdue[0]), "since": overdue[1]}


async def request_for_proposal(db: AsyncSession, proposal_id: uuid.UUID) -> LookupRequest | None:
    """The latest lookup asked for a proposal, for its "Asked … ago" state."""
    return (
        await db.execute(
            select(LookupRequest)
            .where(LookupRequest.proposal_id == proposal_id)
            .order_by(LookupRequest.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
