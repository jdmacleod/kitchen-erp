"""Possible duplicate products (2P, 03): criteria 106 and 107.

Every product here is invented.
"""

from __future__ import annotations

import uuid

from tests.pricebook_helpers import make_product


async def _product(client, ingredient_id: str, name: str, **extra) -> dict:
    r = await client.post(
        "/api/v1/products", json={"ingredient_id": ingredient_id, "name": name, **extra}
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _pairs(client) -> list[tuple[str, str]]:
    r = await client.get("/api/v1/products/duplicates")
    assert r.status_code == 200, r.text
    return [tuple(sorted((i["a"]["id"], i["b"]["id"]))) for i in r.json()["items"]]


async def test_only_the_same_product_is_offered(admin_client):
    """Criterion 106: a duplicate is offered; a size, a variant and a merged product never."""
    first = await make_product(
        admin_client, "Plum jam", "Plum Jam", brand="Fernhill", pack_qty="8", pack_unit="oz"
    )
    ing = first["ingredient"]["id"]
    twin = await _product(
        admin_client,
        ing,
        "Fernhill\u2122 plum jam",
        brand="Fernhill",
        pack_qty="227",
        pack_unit="g",
    )
    await _product(admin_client, ing, "Plum Jam", brand="Fernhill", pack_qty="16", pack_unit="oz")
    await _product(admin_client, ing, "Spicy Plum Jam", brand="Fernhill")
    assert await _pairs(admin_client) == [tuple(sorted((first["id"], twin["id"])))]

    inbox = (await admin_client.get("/api/v1/inbox")).json()["items"]
    [row] = [i for i in inbox if i["kind"] == "duplicates"]
    assert row["title"] == "1 possible duplicate product"
    assert row["action_route"] == "/catalog/products?duplicates=1"

    # Merging either product removes the pair (criterion 107).
    r = await admin_client.post(
        f"/api/v1/products/{twin['id']}/merge", json={"survivor_id": first["id"]}
    )
    assert r.status_code == 200, r.text
    assert await _pairs(admin_client) == []
    inbox = (await admin_client.get("/api/v1/inbox")).json()["items"]
    assert not [i for i in inbox if i["kind"] == "duplicates"]


async def test_not_the_same_is_remembered_in_either_order(admin_client):
    """Criterion 107."""
    first = await make_product(admin_client, "Kelp chips", "Kelp Chips")
    second = await _product(admin_client, first["ingredient"]["id"], "kelp chips")
    assert len(await _pairs(admin_client)) == 1
    body = {"a": second["id"], "b": first["id"]}
    r = await admin_client.post("/api/v1/products/duplicates/distinct", json=body)
    assert r.status_code == 204, r.text
    assert await _pairs(admin_client) == []
    # Saying it again, in the other order, changes nothing.
    body = {"a": first["id"], "b": second["id"]}
    r = await admin_client.post("/api/v1/products/duplicates/distinct", json=body)
    assert r.status_code == 204, r.text


async def test_not_the_same_needs_two_existing_products(admin_client):
    first = await make_product(admin_client, "Kelp chips", "Kelp Chips")
    same = {"a": first["id"], "b": first["id"]}
    r = await admin_client.post("/api/v1/products/duplicates/distinct", json=same)
    assert r.status_code == 422 and r.json()["error"]["code"] == "same_product"
    missing = {"a": first["id"], "b": str(uuid.uuid4())}
    r = await admin_client.post("/api/v1/products/duplicates/distinct", json=missing)
    assert r.status_code == 404
