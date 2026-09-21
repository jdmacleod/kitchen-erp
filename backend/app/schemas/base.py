"""Shared schema conventions. Decimals always cross the wire as strings."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer


def _decimal_to_str(value: Decimal) -> str:
    return format(value, "f")


# Use for every money or quantity field in a response schema. Pydantic v2 already
# serializes Decimal as a string in JSON mode; this makes the intent explicit and
# survives any change of default.
DecimalStr = Annotated[Decimal, PlainSerializer(_decimal_to_str, return_type=str, when_used="json")]


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")
