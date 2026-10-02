"""The products helper contract (04, 2N).

The helper's routes are the only ones a ``products:read`` or ``products:suggest``
token reaches; every other route refuses it (default-deny, as in 1F). The
person's routes ask for a lookup and decide refreshed posted prices.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import FileResponse

from app.api.deps import CurrentUser, DbSession, scoped_user, token_id
from app.api.product_photos import _read
from app.models import AppUser
from app.schemas.lookups import (
    AnswerOut,
    HelperStatus,
    LookupQueue,
    LookupRequestOut,
    PriceChangeDecided,
    PriceChangeDecision,
    PriceChangeList,
    PricesReported,
)
from app.schemas.product_photos import ProductPhotoOut
from app.services import lookups

router = APIRouter(tags=["products helper"])

HelperReader = Annotated[AppUser, Depends(scoped_user("products:read"))]
HelperWriter = Annotated[AppUser, Depends(scoped_user("products:suggest"))]


# --- the helper (products:read, products:suggest) ------------------------------------------


@router.get("/lookup-requests", response_model=LookupQueue)
async def lookup_queue(_: HelperReader, db: DbSession) -> LookupQueue:
    """Open lookup requests, oldest first: the one thing products:read may read."""
    return LookupQueue(
        items=[LookupRequestOut.model_validate(r) for r in await lookups.open_requests(db)]
    )


@router.get("/lookup-requests/{request_id}/original", response_class=FileResponse)
async def lookup_original(request_id: uuid.UUID, _: HelperReader, db: DbSession) -> FileResponse:
    """A photo's original, only through an open cutout request for it."""
    path = await lookups.cutout_original(db, request_id)
    media_type = "image/png" if path.suffix == ".png" else "image/jpeg"
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "no-store"})


@router.post("/lookup-requests/{request_id}/mask", response_model=ProductPhotoOut)
async def lookup_mask(
    request_id: uuid.UUID,
    request: Request,
    _: HelperWriter,
    db: DbSession,
    mask: Annotated[UploadFile, File(description="single-channel PNG, the photo's size")],
) -> ProductPhotoOut:
    """A cutout mask for an open cutout request; the photo gets cutout_source = tool."""
    image = await lookups.answer_cutout(db, token_id(request), request_id, await _read(mask))
    return ProductPhotoOut.model_validate(image)


@router.post("/lookup-answers", response_model=AnswerOut)
async def lookup_answer(request: Request, _: HelperWriter, db: DbSession) -> AnswerOut:
    """An answer in ``kitchen-erp-products/1``. Refused answers are recorded too."""
    body = lookups.parse_body(await request.body())
    outcome, proposal_id = await lookups.answer(db, token_id(request), body)
    return AnswerOut(outcome=outcome, proposal_id=proposal_id)  # type: ignore[arg-type]


@router.post("/listing-price-changes", response_model=PricesReported)
async def report_listing_prices(request: Request, _: HelperWriter, db: DbSession) -> PricesReported:
    """Posted prices that changed; each waits for a person before it is recorded."""
    body = lookups.parse_body(await request.body())
    return PricesReported(added=await lookups.report_prices(db, token_id(request), body))


# --- people -------------------------------------------------------------------------------


@router.get("/products-helper", response_model=HelperStatus)
async def products_helper(_: CurrentUser, db: DbSession) -> HelperStatus:
    return HelperStatus(configured=await lookups.helper_configured(db))


@router.post("/product-proposals/{proposal_id}/look-up", response_model=LookupRequestOut)
async def look_up(proposal_id: uuid.UUID, user: CurrentUser, db: DbSession) -> LookupRequestOut:
    """ "Look this up online": hand the proposal's barcode or page to the helper."""
    return LookupRequestOut.model_validate(await lookups.ask_for_proposal(db, user, proposal_id))


@router.get("/listing-price-changes", response_model=PriceChangeList)
async def price_changes(_: CurrentUser, db: DbSession) -> PriceChangeList:
    return PriceChangeList(items=await lookups.pending_price_changes(db))  # type: ignore[arg-type]


@router.post("/listing-price-changes/{change_id}/accept", response_model=PriceChangeDecided)
async def accept_price_change(
    change_id: uuid.UUID, payload: PriceChangeDecision, user: CurrentUser, db: DbSession
) -> PriceChangeDecided:
    change = await lookups.decide_price_change(
        db, user, change_id, accept=True, vendor_location_id=payload.vendor_location_id
    )
    return PriceChangeDecided.model_validate(change)


@router.post("/listing-price-changes/{change_id}/reject", response_model=PriceChangeDecided)
async def reject_price_change(
    change_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> PriceChangeDecided:
    change = await lookups.decide_price_change(db, user, change_id, accept=False)
    return PriceChangeDecided.model_validate(change)
