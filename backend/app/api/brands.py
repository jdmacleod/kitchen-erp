"""Store-brand families (spec 16, 2R): the list a vendor's family is chosen from."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession
from app.models.brands import BrandFamily
from app.schemas.brands import BrandFamilyList
from app.schemas.geo import BrandFamilyRef

router = APIRouter(prefix="/brand-families", tags=["brands"])


@router.get("", response_model=BrandFamilyList)
async def list_brand_families(_: CurrentUser, db: DbSession) -> BrandFamilyList:
    """Every imported family, by name; retailers are the ones a vendor can belong to."""
    rows = (await db.execute(select(BrandFamily).order_by(BrandFamily.name))).scalars()
    return BrandFamilyList(items=[BrandFamilyRef.model_validate(f) for f in rows])
