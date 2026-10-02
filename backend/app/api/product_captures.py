"""Barcode lookups and "Photograph a product" (04, 2L)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile, status
from fastapi.responses import JSONResponse

from app.api.deps import CurrentUser, DbSession, Idempotency
from app.api.product_photos import _read
from app.api.product_proposals import proposal_out
from app.core.errors import ApiError
from app.schemas.captures import (
    AddressIn,
    AddressPreviewOut,
    BarcodeLookupIn,
    BarcodeLookupOut,
    PageCaptureIn,
)
from app.schemas.catalog import ProductOut
from app.schemas.product_photos import PhotoRole
from app.schemas.proposals import ProposalOut
from app.services import barcode_lookup, page_captures, proposals
from app.services.product_photos import MAX_PHOTOS_PER_UPLOAD, PhotoUpload

router = APIRouter(tags=["product captures"])


@router.post("/barcode-lookups", response_model=BarcodeLookupOut)
async def lookup_barcode(
    payload: BarcodeLookupIn, user: CurrentUser, db: DbSession
) -> BarcodeLookupOut:
    """The catalog first; a weighed-item label needs its store; an unknown GTIN proposes."""
    found = await barcode_lookup.lookup(
        db,
        user,
        payload.code,
        symbology=payload.symbology,
        vendor_location_id=payload.vendor_location_id,
        lat=payload.lat,
        lon=payload.lon,
    )
    return BarcodeLookupOut(
        result=found.result,  # type: ignore[arg-type]
        product=ProductOut.model_validate(found.product) if found.product else None,
        proposal_id=found.proposal_id,
        label=found.label,  # type: ignore[arg-type]
        vendor_location_id=found.vendor_location_id,
        needs_store=found.needs_store,
    )


def _decimal(value: str | None, name: str) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        raise ApiError(422, "validation_error", f"{name} must be a decimal string.") from None


@router.post(
    "/product-captures/photos",
    response_model=ProposalOut,
    status_code=status.HTTP_201_CREATED,
    responses={200: {"model": ProposalOut, "description": "the same photos, already pending"}},
)
async def photograph_product(
    user: CurrentUser,
    db: DbSession,
    photos: Annotated[list[UploadFile], File(description="one to four photos")],
    roles: Annotated[list[PhotoRole] | None, Form(description="one per photo, in order")] = None,
    captured_at: Annotated[datetime | None, Form()] = None,
    lat: Annotated[str | None, Form()] = None,
    lon: Annotated[str | None, Form()] = None,
) -> JSONResponse:
    """Up to four photos of one product become one proposal, read in the background."""
    if roles is not None and len(roles) != len(photos):
        raise ApiError(422, "validation_error", "Give one role per photo, or none.")
    if len(photos) > MAX_PHOTOS_PER_UPLOAD:
        raise ApiError(422, "validation_error", "Add between one and four photos at a time.")
    lat_d, lon_d = _decimal(lat, "lat"), _decimal(lon, "lon")
    if (lat_d is None) != (lon_d is None):
        raise ApiError(422, "validation_error", "lat and lon must be given together.")
    roles = roles or ["product"] * len(photos)
    uploads = [PhotoUpload(await _read(p), r) for p, r in zip(photos, roles, strict=True)]
    result = await proposals.capture_photos(
        db, user, uploads, captured_at=captured_at, lat=lat_d, lon=lon_d
    )
    body = await proposal_out(db, result.proposal)
    return JSONResponse(
        status_code=201 if result.created else 200, content=body.model_dump(mode="json")
    )


@router.post(
    "/product-captures",
    response_model=ProposalOut,
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {"model": ProposalOut, "description": "the same page, already pending"},
        413: {"description": "the page text or product data is too large"},
    },
)
async def capture_page(
    payload: PageCaptureIn, user: CurrentUser, db: DbSession, guard: Idempotency
) -> JSONResponse:
    """A vendor page from the bookmarklet, or a pasted address, becomes a proposal."""
    if guard.replay is not None:
        return guard.replay
    result = await page_captures.capture_page(
        db,
        user,
        page_captures.PageCapture(
            page_url=payload.page_url,
            channel=payload.channel,
            canonical_url=payload.canonical_url,
            title=payload.title,
            meta=payload.meta,
            structured_data=payload.structured_data,
            dom_text=payload.dom_text,
            image_urls=payload.image_urls,
            images=[(i.url, i.data_base64) for i in payload.images],
            vendor_id=payload.vendor_id,
            without_store=payload.without_store,
        ),
    )
    body = (await proposal_out(db, result.proposal)).model_dump(mode="json")
    return await guard.commit(201 if result.created else 200, body)


@router.post("/product-captures/address", response_model=AddressPreviewOut)
async def preview_address(payload: AddressIn, _: CurrentUser, db: DbSession) -> AddressPreviewOut:
    """The vendor, title and item number an address says; nothing is fetched."""
    found = await page_captures.preview(db, payload.page_url)
    return AddressPreviewOut(
        vendor={"id": found.vendor.id, "name": found.vendor.name} if found.vendor else None,  # type: ignore[arg-type]
        canonical_url=found.canonical_url,
        title=found.title,
        item_number=found.item_number,
    )
