from __future__ import annotations

from app.schemas.base import ApiModel, DecimalStr


class UnitOut(ApiModel):
    code: str
    dimension: str
    to_base_factor: DecimalStr
    system: str
    aliases: list[str]


class UnitList(ApiModel):
    items: list[UnitOut]


class UnitParseOut(ApiModel):
    text: str
    code: str | None
    error: str | None = None
