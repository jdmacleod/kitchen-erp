"""Non-negotiable 1: decimals cross the wire as strings."""

import json
from decimal import Decimal

import httpx
from fastapi import APIRouter

from app.main import app
from app.schemas.base import ApiModel, DecimalStr


class PriceOut(ApiModel):
    price: DecimalStr
    qty: DecimalStr


_router = APIRouter()


@_router.get("/__test/decimal", response_model=PriceOut)
async def _decimal() -> PriceOut:
    return PriceOut(price=Decimal("3.99"), qty=Decimal("2.310"))


app.include_router(_router)


def test_schema_dumps_decimal_as_string():
    dumped = json.loads(PriceOut(price=Decimal("3.99"), qty=Decimal("0.5")).model_dump_json())
    assert dumped == {"price": "3.99", "qty": "0.5"}


async def test_serialized_response_carries_strings(client: httpx.AsyncClient):
    r = await client.get("/__test/decimal")
    assert r.status_code == 200
    assert r.text == '{"price":"3.99","qty":"2.310"}'
    assert isinstance(r.json()["price"], str)
