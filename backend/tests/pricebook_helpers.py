"""Helpers for price-book tests. Synthetic geography and invented vendors only."""

from __future__ import annotations

import httpx

SYNTH = [("33.500000", "-120.500000"), ("33.600000", "-120.400000"), ("33.700000", "-120.300000")]


async def make_location(
    client: httpx.AsyncClient,
    vendor_name: str,
    name: str,
    *,
    kind: str = "independent",
    price_scope: str = "location",
    coords=SYNTH[0],
    vendor_id: str | None = None,
) -> dict:
    body = {"name": name, "lat": coords[0], "lon": coords[1]}
    if vendor_id:
        body["vendor_id"] = vendor_id
    else:
        body["vendor"] = {"name": vendor_name, "kind": kind, "price_scope": price_scope}
    r = await client.post("/api/v1/vendor-locations", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def make_product(
    client: httpx.AsyncClient, ingredient_name: str, product_name: str, **extra
) -> dict:
    r = await client.post(
        "/api/v1/products",
        json={"ingredient": {"name": ingredient_name}, "name": product_name, **extra},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def shelf(
    client: httpx.AsyncClient, product_id: str, location_id: str, price: str, **extra
) -> dict:
    body = {
        "product_id": product_id,
        "vendor_location_id": location_id,
        "price": price,
        "unit": "each",
        **extra,
    }
    r = await client.post("/api/v1/price-observations", json=body)
    assert r.status_code == 201, r.text
    return r.json()
