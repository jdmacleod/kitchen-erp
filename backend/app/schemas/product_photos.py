"""Product photos (03, 1I)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field, computed_field

from app.schemas.base import ApiModel
from app.services import media

PhotoRole = Literal["product", "label_front", "label_nutrition", "label_ingredients", "shelf_tag"]
PhotoStatus = Literal["processing", "candidate", "active", "hidden", "failed"]
PhotoSource = Literal["user_photo", "manufacturer", "open_food_facts", "vendor_listing"]


class PhotoUrls(ApiModel):
    """Versioned media addresses; a cutout is present only when the photo has one."""

    small: str
    medium: str
    large: str
    cutout_medium: str | None = None
    cutout_large: str | None = None


def _urls(sha: str | None, mask_sha: str | None) -> PhotoUrls | None:
    if sha is None:
        return None
    return PhotoUrls(
        small=media.url(sha, "160"),
        medium=media.url(sha, "480"),
        large=media.url(sha, "1200"),
        cutout_medium=media.url(sha, "cutout-480", mask_sha) if mask_sha else None,
        cutout_large=media.url(sha, "cutout-1200", mask_sha) if mask_sha else None,
    )


class PhotoSummary(ApiModel):
    """A product's main photo, as a list or a header shows it."""

    id: uuid.UUID
    sha256: str | None = Field(exclude=True)
    mask_sha256: str | None = Field(exclude=True)
    width: int | None
    height: int | None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_cutout(self) -> bool:
        return self.mask_sha256 is not None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def urls(self) -> PhotoUrls | None:
        return _urls(self.sha256, self.mask_sha256)


class ProductPhotoOut(PhotoSummary):
    product_id: uuid.UUID | None
    role: PhotoRole
    status: PhotoStatus
    source_kind: PhotoSource
    source_url: str | None
    attribution: str | None
    cutout_source: Literal["device", "tool"] | None
    pinned: bool
    is_stock_suspect: bool
    ocr_text: str | None
    captured_at: datetime | None
    created_at: datetime
    is_main: bool = False


class ProductPhotoList(ApiModel):
    items: list[ProductPhotoOut]
    primary_image_id: uuid.UUID | None
