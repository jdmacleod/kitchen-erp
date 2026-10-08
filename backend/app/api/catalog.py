"""Ingredients, measures, products, typeahead, USDA suggestions, bridge bench (Phase 1C)."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, Query, Response, status
from fastapi.responses import JSONResponse

from app.api.deps import CurrentUser, DbSession, Idempotency
from app.catalog.categories import CategoryKey
from app.schemas.catalog import (
    ConvertIn,
    ConvertOut,
    IngredientCreate,
    IngredientList,
    IngredientOut,
    IngredientSearchOut,
    IngredientUpdate,
    MeasureCreate,
    MeasureOut,
    MeasureUpdate,
    ProductCreate,
    ProductList,
    ProductMergeIn,
    ProductMergeOut,
    ProductOut,
    ProductUpdate,
    SearchOut,
    UsdaSuggestionList,
)
from app.services import catalog, product_merge, resolution, usda

router = APIRouter(tags=["catalog"])


# --- ingredients ------------------------------------------------------------


@router.get("/ingredients", response_model=IngredientList)
async def list_ingredients(
    _: CurrentUser,
    db: DbSession,
    q: str | None = None,
    include_inactive: bool = False,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
) -> IngredientList:
    rows, next_cursor = await catalog.list_ingredients(
        db, q=q, include_inactive=include_inactive, limit=limit, cursor=cursor
    )
    return IngredientList(
        items=[IngredientOut.model_validate(r) for r in rows], next_cursor=next_cursor
    )


@router.post("/ingredients", response_model=IngredientOut, status_code=status.HTTP_201_CREATED)
async def create_ingredient(
    payload: IngredientCreate, _: CurrentUser, db: DbSession, guard: Idempotency
) -> JSONResponse:
    if guard.replay is not None:
        return guard.replay
    row = await catalog.create_ingredient(db, payload)
    return await guard.commit(201, IngredientOut.model_validate(row).model_dump(mode="json"))


@router.get("/ingredients/search", response_model=IngredientSearchOut)
async def search_ingredients(
    db: DbSession,
    _: CurrentUser,
    q: str = Query(..., min_length=1, max_length=200),
    include_standard: bool = False,
    limit: int = Query(10, ge=1, le=50),
) -> IngredientSearchOut:
    """Ingredients by name or other spelling, best first; standard names on request (1G)."""
    items = await catalog.search_ingredients(db, q, include_standard=include_standard, limit=limit)
    return IngredientSearchOut(items=items)


@router.get("/ingredients/in-text", response_model=IngredientSearchOut)
async def ingredients_in_text(
    db: DbSession,
    _: CurrentUser,
    text: str = Query(..., min_length=1, max_length=200),
) -> IngredientSearchOut:
    """Ingredients a receipt line names outright, for a new product's picker (#88)."""
    return IngredientSearchOut(items=await catalog.ingredients_in_text(db, text))


@router.get("/ingredients/{ingredient_id}", response_model=IngredientOut)
async def get_ingredient(ingredient_id: uuid.UUID, _: CurrentUser, db: DbSession) -> IngredientOut:
    return IngredientOut.model_validate(await catalog.get_ingredient(db, ingredient_id))


@router.patch("/ingredients/{ingredient_id}", response_model=IngredientOut)
async def update_ingredient(
    ingredient_id: uuid.UUID, payload: IngredientUpdate, _: CurrentUser, db: DbSession
) -> IngredientOut:
    return IngredientOut.model_validate(await catalog.update_ingredient(db, ingredient_id, payload))


@router.post("/ingredients/{ingredient_id}/deactivate", response_model=IngredientOut)
async def deactivate_ingredient(
    ingredient_id: uuid.UUID, _: CurrentUser, db: DbSession
) -> IngredientOut:
    return IngredientOut.model_validate(
        await catalog.set_ingredient_active(db, ingredient_id, False)
    )


@router.post("/ingredients/{ingredient_id}/activate", response_model=IngredientOut)
async def activate_ingredient(
    ingredient_id: uuid.UUID, _: CurrentUser, db: DbSession
) -> IngredientOut:
    return IngredientOut.model_validate(
        await catalog.set_ingredient_active(db, ingredient_id, True)
    )


@router.post("/ingredients/{ingredient_id}/density/confirm", response_model=IngredientOut)
async def confirm_density(ingredient_id: uuid.UUID, _: CurrentUser, db: DbSession) -> IngredientOut:
    return IngredientOut.model_validate(await catalog.confirm_density(db, ingredient_id))


@router.post("/ingredients/{ingredient_id}/convert", response_model=ConvertOut)
async def convert_bench(
    ingredient_id: uuid.UUID, payload: ConvertIn, _: CurrentUser, db: DbSession
) -> ConvertOut:
    return await catalog.bench(db, ingredient_id, payload)


# --- measures ---------------------------------------------------------------


@router.post(
    "/ingredients/{ingredient_id}/measures",
    response_model=MeasureOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_measure(
    ingredient_id: uuid.UUID, payload: MeasureCreate, _: CurrentUser, db: DbSession
) -> MeasureOut:
    return MeasureOut.model_validate(await catalog.add_measure(db, ingredient_id, payload))


@router.patch("/measures/{measure_id}", response_model=MeasureOut)
async def update_measure(
    measure_id: uuid.UUID, payload: MeasureUpdate, _: CurrentUser, db: DbSession
) -> MeasureOut:
    return MeasureOut.model_validate(await catalog.update_measure(db, measure_id, payload))


@router.post("/measures/{measure_id}/confirm", response_model=MeasureOut)
async def confirm_measure(measure_id: uuid.UUID, _: CurrentUser, db: DbSession) -> MeasureOut:
    return MeasureOut.model_validate(await catalog.confirm_measure(db, measure_id))


@router.delete("/measures/{measure_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_measure(measure_id: uuid.UUID, _: CurrentUser, db: DbSession) -> Response:
    await catalog.delete_measure(db, measure_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- products ---------------------------------------------------------------


@router.get("/products", response_model=ProductList)
async def list_products(
    _: CurrentUser,
    db: DbSession,
    ingredient_id: uuid.UUID | None = None,
    include_inactive: bool = False,
    q: str | None = Query(
        default=None,
        min_length=1,
        max_length=200,
        description="name, brand, ingredient or barcode; ranks like /products/search, one page",
    ),
    # A category key, or "none" for products whose ingredient has no category.
    category: CategoryKey | Literal["none"] | None = None,
    barcode: str | None = Query(default=None, description="exact barcode match"),
    no_photo: bool = Query(default=False, description="only products with no main photo"),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
) -> ProductList:
    rows, next_cursor = await catalog.list_products(
        db,
        barcode=barcode,
        no_photo=no_photo,
        ingredient_id=ingredient_id,
        include_inactive=include_inactive,
        q=q,
        category=category,
        limit=limit,
        cursor=cursor,
    )
    return ProductList(items=rows, next_cursor=next_cursor)


@router.get("/products/search", response_model=SearchOut)
async def search_products(
    _: CurrentUser, db: DbSession, q: str = Query(min_length=1), limit: int = Query(10, ge=1, le=50)
) -> SearchOut:
    return SearchOut(items=await catalog.search_products(db, q, limit))


@router.post("/products", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
async def create_product(
    payload: ProductCreate, user: CurrentUser, db: DbSession, guard: Idempotency
) -> JSONResponse:
    if guard.replay is not None:
        return guard.replay
    row = await catalog.create_product(db, payload)
    if payload.barcode:
        await resolution.match_waiting(db, user, row.id)
        row = await catalog.get_product(db, row.id)
    return await guard.commit(201, ProductOut.model_validate(row).model_dump(mode="json"))


@router.get("/products/{product_id}", response_model=ProductOut)
async def get_product(product_id: uuid.UUID, _: CurrentUser, db: DbSession) -> ProductOut:
    return ProductOut.model_validate(await catalog.get_product(db, product_id))


@router.patch("/products/{product_id}", response_model=ProductOut)
async def update_product(
    product_id: uuid.UUID, payload: ProductUpdate, user: CurrentUser, db: DbSession
) -> ProductOut:
    row = await catalog.update_product(db, product_id, payload)
    if payload.barcode:
        await resolution.match_waiting(db, user, product_id)
        row = await catalog.get_product(db, product_id)
    return ProductOut.model_validate(row)


@router.post("/products/{product_id}/deactivate", response_model=ProductOut)
async def deactivate_product(product_id: uuid.UUID, _: CurrentUser, db: DbSession) -> ProductOut:
    return ProductOut.model_validate(await catalog.set_product_active(db, product_id, False))


@router.post("/products/{product_id}/activate", response_model=ProductOut)
async def activate_product(product_id: uuid.UUID, _: CurrentUser, db: DbSession) -> ProductOut:
    return ProductOut.model_validate(await catalog.set_product_active(db, product_id, True))


@router.post("/products/{product_id}/merge/preview", response_model=ProductMergeOut)
async def merge_product_preview(
    product_id: uuid.UUID, body: ProductMergeIn, _: CurrentUser, db: DbSession
) -> ProductMergeOut:
    """What merging this product into another would do; nothing is written (#179)."""
    done = await product_merge.merge_preview(db, body.survivor_id, product_id)
    return ProductMergeOut(**asdict(done))


@router.post("/products/{product_id}/merge", response_model=ProductMergeOut)
async def merge_product(
    product_id: uuid.UUID, body: ProductMergeIn, _: CurrentUser, db: DbSession
) -> ProductMergeOut:
    """Merge this product into ``survivor_id``: it becomes inactive, and its prices,
    codes, listings, photos and receipt wordings belong to the survivor (#179)."""
    done = await product_merge.merge(db, body.survivor_id, product_id)
    return ProductMergeOut(**asdict(done))


@router.post("/products/{product_id}/density-override/confirm", response_model=ProductOut)
async def confirm_density_override(
    product_id: uuid.UUID, _: CurrentUser, db: DbSession
) -> ProductOut:
    return ProductOut.model_validate(await catalog.confirm_density_override(db, product_id))


# --- USDA suggestions -------------------------------------------------------


@router.get("/usda/suggestions", response_model=UsdaSuggestionList)
async def usda_suggestions(
    _: CurrentUser,
    db: DbSession,
    name: str = Query(min_length=1),
    limit: int = Query(5, ge=1, le=20),
) -> UsdaSuggestionList:
    loaded = await usda.is_loaded(db)
    items = await usda.suggest(db, name, limit) if loaded else []
    return UsdaSuggestionList(items=items, loaded=loaded)
