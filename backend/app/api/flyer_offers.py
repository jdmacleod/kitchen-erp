"""Flyer offers (18): the offer queue (2U-3), and this week's linked offers and a
product's offer history (2U-4)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.core.errors import ApiError
from app.models import Product
from app.schemas.flyers import (
    CurrentOffers,
    OfferDecided,
    OfferDecision,
    OfferHistory,
    OfferQueue,
)
from app.services import flyer_offers, flyer_reads, flyers

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


@router.get("/flyer-offers/current", response_model=CurrentOffers)
async def current_offers(
    _: CurrentUser,
    db: DbSession,
    home_base_id: Annotated[uuid.UUID, Query(description="the kitchen; never inferred")],
    on: Annotated[date | None, Query(description="the day; today when left out")] = None,
) -> CurrentOffers:
    """The kitchen's linked offers valid that day, each with its price per unit (U9)."""
    items = await flyer_reads.current_offers(db, home_base_id, on or flyers.today())
    return CurrentOffers.model_validate({"items": items})


@router.get("/products/{product_id}/flyer-offers", response_model=OfferHistory)
async def offer_history(
    product_id: uuid.UUID,
    _: CurrentUser,
    db: DbSession,
    vendor_location_id: uuid.UUID | None = None,
) -> OfferHistory:
    """Every linked offer of a product, newest week first, at one store or all, with
    the product's typical price over the last twelve weeks (X3)."""
    if await db.get(Product, product_id) is None:
        raise ApiError(404, "not_found", "No such product.")
    history = await flyer_reads.offer_history(db, product_id, flyers.today(), vendor_location_id)
    return OfferHistory.model_validate(history)
