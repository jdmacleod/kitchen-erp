"""The offer queue (18, 2U-3): match suggested flyer offers to products."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.schemas.flyers import OfferDecided, OfferDecision, OfferQueue
from app.services import flyer_offers, flyers

router = APIRouter(tags=["flyers"])


@router.get("/flyer-offers/queue", response_model=OfferQueue)
async def offer_queue(_: CurrentUser, db: DbSession) -> OfferQueue:
    """Current suggested offers, one group per vendor and printed name (U7, F13)."""
    return OfferQueue.model_validate(await flyer_offers.offer_queue(db, flyers.today()))


@router.post("/flyer-offers/decide", response_model=OfferDecided)
async def decide(payload: OfferDecision, user: CurrentUser, db: DbSession) -> OfferDecided:
    """Use a product for every current offer of that name, or mark it not wanted; the
    name is learned for next week."""
    n = await flyer_offers.decide(
        db,
        user.id,
        vendor_id=payload.vendor_id,
        name_norm=payload.name_norm,
        product_id=payload.product_id,
        ignore=payload.ignore,
        today=flyers.today(),
    )
    return OfferDecided(decided=n)
