""" "Already in your catalog" on every product create (#348). Everything here is invented."""

from __future__ import annotations

from tests.catalog_helpers import make_ingredient, make_product, seed_units_via_service


async def _seed(admin_client, db_session):
    await seed_units_via_service(db_session)
    bacon = await make_ingredient(admin_client, "lantern bacon")
    other = await make_ingredient(admin_client, "smoked lantern pork")
    existing = await make_product(
        admin_client,
        bacon["id"],
        "Thick cut lantern bacon",
        brand="Harbor Light",
        pack_qty="12",
        pack_unit="oz",
    )
    return bacon, other, existing


async def test_the_same_product_under_another_ingredient_is_refused(admin_client, db_session):
    _, other, existing = await _seed(admin_client, db_session)
    body = {
        "ingredient_id": other["id"],
        "name": "Thick Cut Lantern Bacon",
        "brand": "Harbor Light",
        "pack_qty": "12",
        "pack_unit": "oz",
    }
    r = await admin_client.post("/api/v1/products", json=body)
    assert r.status_code == 409, r.text
    error = r.json()["error"]
    assert error["code"] == "product_exists"
    assert error["details"]["product_id"] == existing["id"]
    assert error["details"]["candidates"][0]["verdict"] == "same"
    # Said to be new anyway, it is created.
    r = await admin_client.post("/api/v1/products", json={**body, "allow_duplicate": True})
    assert r.status_code == 201, r.text


async def test_another_size_is_not_refused(admin_client, db_session):
    bacon, _, _ = await _seed(admin_client, db_session)
    r = await admin_client.post(
        "/api/v1/products",
        json={
            "ingredient_id": bacon["id"],
            "name": "Thick cut lantern bacon",
            "brand": "Harbor Light",
            "pack_qty": "24",
            "pack_unit": "oz",
        },
    )
    assert r.status_code == 201, r.text


async def test_a_different_product_is_not_refused(admin_client, db_session):
    bacon, _, _ = await _seed(admin_client, db_session)
    r = await admin_client.post(
        "/api/v1/products",
        json={"ingredient_id": bacon["id"], "name": "Peppered lantern jerky", "brand": "Fernhill"},
    )
    assert r.status_code == 201, r.text


async def test_bulk_naming_finds_the_same_product_under_another_ingredient(
    admin_client, admin, db_session
):
    from tests.test_naming import _queue

    loc = await _queue(admin_client, admin, db_session)
    flour = (await make_ingredient(admin_client, "bread flour"))["id"]
    other = (await make_ingredient(admin_client, "strong flour"))["id"]
    existing = await make_product(admin_client, flour, "Riverbend bread flour")
    row = {"vendor_id": loc["vendor"]["id"], "raw_text_norm": "RVRBND BREAD FLR 2KG",
           "name": "Riverbend Bread Flour", "ingredient_id": other}  # fmt: skip
    r = await admin_client.post("/api/v1/to-identify/name-products", json={"rows": [row]})
    [result] = r.json()["results"]
    assert result["error"]["code"] == "product_exists"
    assert result["error"]["product"]["id"] == existing["id"]
