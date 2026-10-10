"""Store-brand families in the API (spec 16, 2R)."""

from __future__ import annotations

from app.schemas.base import ApiModel
from app.schemas.geo import BrandFamilyRef


class BrandFamilyList(ApiModel):
    items: list[BrandFamilyRef]
