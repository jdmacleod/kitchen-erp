"""Product photos (03, 1I): add, list, choose the main photo, hide, retry, masks."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, UploadFile, status

from app.api.deps import CurrentUser, DbSession
from app.core.errors import ApiError
from app.schemas.product_photos import PhotoRole, ProductPhotoList, ProductPhotoOut
from app.services import product_photos
from app.services.product_photos import PHOTO_MAX_BYTES, PhotoUpload

router = APIRouter(tags=["product photos"])

_CHUNK = 1024 * 1024


async def _read(upload: UploadFile) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while chunk := await upload.read(_CHUNK):
        size += len(chunk)
        if size > PHOTO_MAX_BYTES:
            raise ApiError(413, "payload_too_large", "The photo exceeds the size limit.")
        chunks.append(chunk)
    return b"".join(chunks)


def _out(image, primary_image_id: uuid.UUID | None) -> ProductPhotoOut:
    return ProductPhotoOut.model_validate(image).model_copy(
        update={"is_main": image.id == primary_image_id}
    )


async def _list(db: DbSession, product_id: uuid.UUID) -> ProductPhotoList:
    product = await product_photos.get_product(db, product_id)
    images = await product_photos.images_of(db, product_id)
    return ProductPhotoList(
        items=[_out(i, product.primary_image_id) for i in images],
        primary_image_id=product.primary_image_id,
    )


@router.post(
    "/product-photos", response_model=ProductPhotoList, status_code=status.HTTP_201_CREATED
)
async def add_product_photos(
    _: CurrentUser,
    db: DbSession,
    product_id: Annotated[uuid.UUID, Form()],
    photos: Annotated[list[UploadFile], File(description="one to four photos")],
    roles: Annotated[list[PhotoRole] | None, Form(description="one per photo, in order")] = None,
    captured_at: Annotated[datetime | None, Form()] = None,
) -> ProductPhotoList:
    """Add up to four photos to a product. Each is prepared in the background."""
    if roles is not None and len(roles) != len(photos):
        raise ApiError(422, "validation_error", "Give one role per photo, or none.")
    roles = roles or ["product"] * len(photos)
    if len(photos) > product_photos.MAX_PHOTOS_PER_UPLOAD:
        raise ApiError(422, "validation_error", "Add between one and four photos at a time.")
    uploads = [PhotoUpload(await _read(p), r) for p, r in zip(photos, roles, strict=True)]
    await product_photos.add_photos(db, product_id, uploads, captured_at=captured_at)
    return await _list(db, product_id)


@router.get("/products/{product_id}/photos", response_model=ProductPhotoList)
async def list_product_photos(
    product_id: uuid.UUID, _: CurrentUser, db: DbSession
) -> ProductPhotoList:
    """Every photo of a product, hidden and failed ones included, oldest first."""
    return await _list(db, product_id)


async def _after(db: DbSession, image) -> ProductPhotoOut:
    product = await product_photos.get_product(db, image.product_id)
    return _out(image, product.primary_image_id)


@router.post("/product-photos/{image_id}/use-as-main", response_model=ProductPhotoOut)
async def use_as_main(image_id: uuid.UUID, _: CurrentUser, db: DbSession) -> ProductPhotoOut:
    return await _after(db, await product_photos.use_as_main(db, image_id))


@router.post("/product-photos/{image_id}/unset-main", response_model=ProductPhotoOut)
async def unset_main(image_id: uuid.UUID, _: CurrentUser, db: DbSession) -> ProductPhotoOut:
    """Give the choice back to the rule."""
    return await _after(db, await product_photos.unset_main(db, image_id))


@router.post("/product-photos/{image_id}/hide", response_model=ProductPhotoOut)
async def hide_photo(image_id: uuid.UUID, _: CurrentUser, db: DbSession) -> ProductPhotoOut:
    return await _after(db, await product_photos.hide(db, image_id))


@router.post("/product-photos/{image_id}/show", response_model=ProductPhotoOut)
async def show_photo(image_id: uuid.UUID, _: CurrentUser, db: DbSession) -> ProductPhotoOut:
    return await _after(db, await product_photos.show(db, image_id))


@router.post("/product-photos/{image_id}/retry", response_model=ProductPhotoOut)
async def retry_photo(image_id: uuid.UUID, _: CurrentUser, db: DbSession) -> ProductPhotoOut:
    return await _after(db, await product_photos.retry(db, image_id))


@router.post(
    "/product-photos/{image_id}/mask",
    response_model=ProductPhotoOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def add_mask(
    image_id: uuid.UUID,
    _: CurrentUser,
    db: DbSession,
    mask: Annotated[UploadFile, File(description="single-channel PNG, the photo's size")],
    source: Annotated[Literal["device", "tool"], Form()] = "tool",
) -> ProductPhotoOut:
    """Queue a mask for a processed photo; its cutouts are made in the background."""
    image = await product_photos.add_mask(db, image_id, await _read(mask), source=source)
    return await _after(db, image)
