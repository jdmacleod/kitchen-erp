"""Purchases, price observations, review, and price-book views (Phase 2A/2B/2D/2E)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status
from fastapi.responses import JSONResponse

from app.api.deps import CurrentUser, DbSession, Idempotency
from app.schemas.pricebook_views import (
    CheapestOut,
    CompareIn,
    CompareOut,
    IngredientPriceHistory,
    LocationPanel,
    OfferList,
    ProductHistory,
)
from app.schemas.purchases import (
    LastUnitOut,
    LineAdd,
    LineDecision,
    LineEdit,
    LineOut,
    ManualPurchaseIn,
    NeedsBridgeItem,
    NeedsBridgeList,
    ObservationCreate,
    ObservationList,
    ObservationOut,
    PurchaseHeaderEdit,
    PurchaseList,
    PurchaseOut,
    QueueApplied,
    QueueApply,
    QueueList,
    RecomputeOut,
    VoidIn,
)
from app.services import catalog, pricebook, pricebook_views, purchases, resolution, review

router = APIRouter(tags=["purchases"])


def observation_out(o) -> ObservationOut:
    return ObservationOut(
        id=o.id,
        product={
            "id": o.product.id,
            "name": o.product.name,
            "brand": o.product.brand,
            "pack_qty": o.product.pack_qty,
            "pack_unit": o.product.pack_unit,
            "ingredient": {
                "id": o.product.ingredient.id,
                "name": o.product.ingredient.name,
                "canonical_unit": o.product.ingredient.canonical_unit,
                "category": o.product.ingredient.category,
            },
        },
        vendor_location={
            "id": o.vendor_location.id,
            "name": o.vendor_location.name,
            "vendor": {
                "id": o.vendor_location.vendor.id,
                "name": o.vendor_location.vendor.name,
                "kind": o.vendor_location.vendor.kind,
                "price_scope": o.vendor_location.vendor.price_scope,
            },
        },
        purchase_line_id=o.purchase_line_id,
        observed_at=o.observed_at,
        price=o.price,
        qty=o.qty,
        unit=o.unit,
        is_promo=o.is_promo,
        source=o.source,
        voided=o.void is not None,
        void_reason=o.void.reason if o.void is not None else None,
        norm=o.norm,
        created_at=o.created_at,
    )


@router.get("/price-observations", response_model=ObservationList)
async def list_observations(
    _: CurrentUser,
    db: DbSession,
    product_id: uuid.UUID | None = None,
    vendor_location_id: uuid.UUID | None = None,
    include_voided: bool = False,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
) -> ObservationList:
    rows, next_cursor = await pricebook.list_observations(
        db,
        product_id=product_id,
        vendor_location_id=vendor_location_id,
        include_voided=include_voided,
        limit=limit,
        cursor=cursor,
    )
    return ObservationList(items=[observation_out(o) for o in rows], next_cursor=next_cursor)


@router.post(
    "/price-observations", response_model=ObservationOut, status_code=status.HTTP_201_CREATED
)
async def create_observation(
    payload: ObservationCreate, user: CurrentUser, db: DbSession, guard: Idempotency
) -> JSONResponse:
    """A shelf price: the simplest producer of observations (capture contract)."""
    if guard.replay is not None:
        return guard.replay
    o = await pricebook.observe(
        db,
        product_id=payload.product_id,
        vendor_location_id=payload.vendor_location_id,
        price=payload.price,
        qty=payload.qty,
        unit=payload.unit,
        source="shelf",
        entered_by=user,
        is_promo=payload.is_promo,
        observed_at=payload.observed_at,
    )
    return await guard.commit(201, observation_out(o).model_dump(mode="json"))


@router.get("/price-observations/{observation_id}", response_model=ObservationOut)
async def get_observation(
    observation_id: uuid.UUID, _: CurrentUser, db: DbSession
) -> ObservationOut:
    return observation_out(await pricebook.get_observation(db, observation_id))


@router.post("/price-observations/{observation_id}/void", response_model=ObservationOut)
async def void_observation(
    observation_id: uuid.UUID, payload: VoidIn, user: CurrentUser, db: DbSession
) -> ObservationOut:
    return observation_out(await pricebook.void(db, observation_id, payload.reason, user))


@router.get("/price-book/needs-bridge", response_model=NeedsBridgeList)
async def needs_bridge(_: CurrentUser, db: DbSession) -> NeedsBridgeList:
    items = [
        NeedsBridgeItem(
            ingredient={
                "id": r["ingredient_id"],
                "name": r["ingredient_name"],
                "canonical_unit": r["canonical_unit"],
                "category": r["category"],
            },
            product={
                "id": r["product_id"],
                "name": r["name"],
                "brand": r["brand"],
                "pack_qty": r["pack_qty"],
                "pack_unit": r["pack_unit"],
                "ingredient": {
                    "id": r["ingredient_id"],
                    "name": r["ingredient_name"],
                    "canonical_unit": r["canonical_unit"],
                    "category": r["category"],
                },
            },
            status=r["status"],
            observation_count=r["observation_count"],
            latest_observed_at=r["latest_observed_at"],
        )
        for r in await pricebook.needs_bridge(db)
    ]
    return NeedsBridgeList(items=items)


@router.post("/price-book/recompute", response_model=RecomputeOut)
async def recompute(_: CurrentUser, db: DbSession) -> RecomputeOut:
    return RecomputeOut(recomputed=await pricebook.recompute_all(db))


# --- purchases --------------------------------------------------------------


async def purchase_out(db, purchase) -> PurchaseOut:
    live = await purchases.live_observations(db, purchase)
    names = await purchases.resolver_names(db, purchase)
    lines = [
        LineOut(
            id=line.id,
            seq=line.seq,
            raw_text=line.raw_text,
            line_kind=line.line_kind,
            product=(
                {
                    "id": line.product.id,
                    "name": line.product.name,
                    "brand": line.product.brand,
                    "pack_qty": line.product.pack_qty,
                    "pack_unit": line.product.pack_unit,
                    "category": line.product.ingredient.category,
                }
                if line.product is not None
                else None
            ),
            parent_line_id=line.parent_line_id,
            qty=line.qty,
            unit=line.unit,
            unit_price=line.unit_price,
            line_total=line.line_total,
            resolution=line.resolution,
            resolved_by=line.resolved_by,
            resolved_by_name=names.get(line.resolved_by) if line.resolved_by else None,
            resolution_confidence=line.resolution_confidence,
            flags=line.flags,
            observation_id=live.get(line.id),
            raw_text_norm=line.raw_text_norm,
            suggestions=line.suggestions or [],
        )
        for line in purchase.lines
    ]
    location = purchase.vendor_location
    return PurchaseOut(
        id=purchase.id,
        vendor_location=(
            {
                "id": location.id,
                "name": location.name,
                "vendor": {
                    "id": location.vendor.id,
                    "name": location.vendor.name,
                    "kind": location.vendor.kind,
                    "price_scope": location.vendor.price_scope,
                },
            }
            if location is not None
            else None
        ),
        receipt_document_id=purchase.receipt_document_id,
        purchased_at=purchase.purchased_at,
        subtotal=purchase.subtotal,
        tax=purchase.tax,
        total=purchase.total,
        computed_total=purchases.computed_total(purchase),
        status=purchase.status,
        source=purchase.source,
        flags=purchase.flags,
        ledger_txn_ref=purchase.ledger_txn_ref,
        lines=lines,
        created_at=purchase.created_at,
        updated_at=purchase.updated_at,
    )


@router.get("/purchases", response_model=PurchaseList)
async def list_purchases(
    _: CurrentUser,
    db: DbSession,
    vendor_location_id: uuid.UUID | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    source: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
) -> PurchaseList:
    rows, next_cursor = await purchases.list_purchases(
        db,
        vendor_location_id=vendor_location_id,
        status=status_filter,
        source=source,
        limit=limit,
        cursor=cursor,
    )
    return PurchaseList(items=[await purchase_out(db, p) for p in rows], next_cursor=next_cursor)


@router.post("/purchases", response_model=PurchaseOut, status_code=status.HTTP_201_CREATED)
async def create_purchase(
    payload: ManualPurchaseIn, user: CurrentUser, db: DbSession, guard: Idempotency
) -> JSONResponse:
    if guard.replay is not None:
        return guard.replay
    purchase = await purchases.create_manual(db, user, payload)
    return await guard.commit(201, (await purchase_out(db, purchase)).model_dump(mode="json"))


@router.get("/purchases/{purchase_id}", response_model=PurchaseOut)
async def get_purchase(purchase_id: uuid.UUID, _: CurrentUser, db: DbSession) -> PurchaseOut:
    return await purchase_out(db, await purchases.get_purchase(db, purchase_id))


@router.put("/purchases/{purchase_id}", response_model=PurchaseOut)
async def update_purchase(
    purchase_id: uuid.UUID, payload: ManualPurchaseIn, user: CurrentUser, db: DbSession
) -> PurchaseOut:
    return await purchase_out(db, await purchases.update_manual(db, user, purchase_id, payload))


@router.get("/products/{product_id}/last-purchase-unit", response_model=LastUnitOut)
async def last_purchase_unit(product_id: uuid.UUID, _: CurrentUser, db: DbSession) -> LastUnitOut:
    return LastUnitOut(unit=await purchases.last_purchase_unit(db, product_id))


# --- review (2D) ------------------------------------------------------------


@router.patch("/purchases/{purchase_id}", response_model=PurchaseOut)
async def edit_header(
    purchase_id: uuid.UUID, payload: PurchaseHeaderEdit, _: CurrentUser, db: DbSession
) -> PurchaseOut:
    return await purchase_out(db, await review.edit_header(db, purchase_id, payload))


@router.post("/purchases/{purchase_id}/lines", response_model=PurchaseOut, status_code=201)
async def add_line(
    purchase_id: uuid.UUID, payload: LineAdd, _: CurrentUser, db: DbSession
) -> PurchaseOut:
    return await purchase_out(db, await review.add_line(db, purchase_id, payload))


@router.patch("/purchases/{purchase_id}/lines/{line_id}", response_model=PurchaseOut)
async def edit_line(
    purchase_id: uuid.UUID, line_id: uuid.UUID, payload: LineEdit, _: CurrentUser, db: DbSession
) -> PurchaseOut:
    return await purchase_out(db, await review.edit_line(db, purchase_id, line_id, payload))


@router.delete("/purchases/{purchase_id}/lines/{line_id}", response_model=PurchaseOut)
async def delete_line(
    purchase_id: uuid.UUID, line_id: uuid.UUID, user: CurrentUser, db: DbSession
) -> PurchaseOut:
    return await purchase_out(db, await review.delete_line(db, user, purchase_id, line_id))


@router.post("/purchases/{purchase_id}/lines/{line_id}/resolve", response_model=PurchaseOut)
async def resolve_line(
    purchase_id: uuid.UUID,
    line_id: uuid.UUID,
    payload: LineDecision,
    user: CurrentUser,
    db: DbSession,
) -> PurchaseOut:
    product_id = payload.product_id
    if payload.product is not None:
        product_id = (await catalog.create_product(db, payload.product)).id
    purchase = await resolution.decide_line(
        db,
        user,
        purchase_id,
        line_id,
        product_id=product_id,
        ignore=payload.ignore,
        accepted_kind=payload.accepted_kind,
    )
    return await purchase_out(db, purchase)


@router.post("/purchases/{purchase_id}/lines/{line_id}/re-resolve", response_model=PurchaseOut)
async def re_resolve_line(
    purchase_id: uuid.UUID, line_id: uuid.UUID, _: CurrentUser, db: DbSession
) -> PurchaseOut:
    return await purchase_out(db, await review.re_resolve_line(db, purchase_id, line_id))


@router.post("/purchases/{purchase_id}/commit", response_model=PurchaseOut)
async def commit_purchase(purchase_id: uuid.UUID, user: CurrentUser, db: DbSession) -> PurchaseOut:
    return await purchase_out(db, await resolution.commit_purchase(db, user, purchase_id))


@router.post("/purchases/{purchase_id}/reopen", response_model=PurchaseOut)
async def reopen_purchase(purchase_id: uuid.UUID, _: CurrentUser, db: DbSession) -> PurchaseOut:
    return await purchase_out(db, await resolution.reopen_purchase(db, purchase_id))


@router.get("/to-identify", response_model=QueueList)
async def to_identify(_: CurrentUser, db: DbSession) -> QueueList:
    groups = await resolution.to_identify(db)
    return QueueList(
        items=[
            {
                "vendor": {"id": g["vendor_id"], "name": g["vendor_name"]},
                "raw_text_norm": g["raw_text_norm"],
                "line_count": g["line_count"],
                "lines": g["lines"],
            }
            for g in groups
        ]
    )


@router.post("/to-identify/apply", response_model=QueueApplied)
async def apply_to_identify(payload: QueueApply, user: CurrentUser, db: DbSession) -> QueueApplied:
    n = await resolution.apply_to_identify(
        db,
        user,
        vendor_id=payload.vendor_id,
        raw_text_norm=payload.raw_text_norm,
        product_id=payload.product_id,
        ignore=payload.ignore,
        line_ids=payload.line_ids,
    )
    return QueueApplied(applied=n)


# --- price book views (2E) --------------------------------------------------


@router.get("/products/{product_id}/prices", response_model=ProductHistory)
async def product_prices(product_id: uuid.UUID, _: CurrentUser, db: DbSession) -> ProductHistory:
    await catalog.get_product(db, product_id)
    return ProductHistory(**await pricebook_views.product_history(db, product_id))


@router.get("/ingredients/{ingredient_id}/price-history", response_model=IngredientPriceHistory)
async def ingredient_price_history(
    ingredient_id: uuid.UUID,
    _: CurrentUser,
    db: DbSession,
    days: int = Query(default=90, ge=1, le=365),
) -> IngredientPriceHistory:
    await catalog.get_ingredient(db, ingredient_id)
    return IngredientPriceHistory(
        **await pricebook_views.ingredient_history(db, ingredient_id, days)
    )


@router.get("/ingredients/{ingredient_id}/offers", response_model=OfferList)
async def ingredient_offers(
    ingredient_id: uuid.UUID,
    _: CurrentUser,
    db: DbSession,
    min_quality: int | None = Query(default=None, ge=1, le=5),
    exclude_stale: bool = False,
    exclude_promo: bool = False,
) -> OfferList:
    await catalog.get_ingredient(db, ingredient_id)
    items = await pricebook_views.ingredient_offers(
        db,
        ingredient_id,
        min_quality=min_quality,
        exclude_stale=exclude_stale,
        exclude_promo=exclude_promo,
    )
    return OfferList(items=items, stale_thresholds=pricebook_views.stale_thresholds())


@router.post("/price-book/compare", response_model=CompareOut)
async def compare(payload: CompareIn, _: CurrentUser, db: DbSession) -> CompareOut:
    result = await pricebook_views.compare(
        db,
        payload.ingredient_ids,
        min_quality=payload.min_quality,
        exclude_stale=payload.exclude_stale,
        exclude_promo=payload.exclude_promo,
    )
    return CompareOut(**result, stale_thresholds=pricebook_views.stale_thresholds())


@router.get("/vendor-locations/{location_id}/price-panel", response_model=LocationPanel)
async def location_price_panel(
    location_id: uuid.UUID, _: CurrentUser, db: DbSession, days: int = Query(30, ge=1, le=3650)
) -> LocationPanel:
    return LocationPanel(**await pricebook_views.location_panel(db, location_id, days))


@router.get("/price-book/cheapest", response_model=CheapestOut)
async def cheapest(
    _: CurrentUser,
    db: DbSession,
    ingredient_id: uuid.UUID,
    min_quality: int | None = Query(default=None, ge=1, le=5),
    exclude_stale: bool = False,
) -> CheapestOut:
    ingredient = await catalog.get_ingredient(db, ingredient_id)
    items = await pricebook_views.cheapest_by_location(
        db, ingredient_id, min_quality=min_quality, exclude_stale=exclude_stale
    )
    return CheapestOut(items=items, unit=ingredient.canonical_unit)
