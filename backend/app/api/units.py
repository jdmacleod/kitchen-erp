from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.schemas.units import UnitList, UnitOut, UnitParseOut
from app.services import units as unit_service
from app.units import UnitParseFailure, parse_unit
from app.units.parse import alias_index

router = APIRouter(prefix="/units", tags=["units"])


@router.get("", response_model=UnitList)
async def list_units(_: CurrentUser, db: DbSession) -> UnitList:
    units = await unit_service.load_units(db)
    return UnitList(items=[UnitOut.model_validate(u) for u in units.values()])


@router.get("/parse", response_model=UnitParseOut)
async def parse(_: CurrentUser, db: DbSession, text: str = Query(min_length=1)) -> UnitParseOut:
    units = await unit_service.load_units(db)
    result = parse_unit(text, alias_index(units.values()))
    if isinstance(result, UnitParseFailure):
        return UnitParseOut(text=text, code=None, error=result.code)
    return UnitParseOut(text=text, code=result)
