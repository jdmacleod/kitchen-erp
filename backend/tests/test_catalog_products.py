import uuid
from decimal import Decimal

import asyncpg
import httpx
import pytest

from tests.catalog_helpers import make_ingredient, make_product, seed_units_via_service


async def test_inline_ingredient_creation_is_transactional(
    admin_client: httpx.AsyncClient, db_session
):
    await seed_units_via_service(db_session)
    r = await admin_client.post(
        "/api/v1/products",
        json={
            "ingredient": {"name": "Rigatoni", "category": "pasta"},
            "brand": "De Cecco",
            "name": "Rigatoni n.24",
            "pack_qty": "1",
            "pack_unit": "lb",
            "barcode": "024094070046",
            "quality_rating": 4,
        },
    )
    assert r.status_code == 201, r.text
    product = r.json()
    assert product["ingredient"]["name"] == "Rigatoni"
    assert product["pack_qty"] == "1" and product["pack_unit"] == "lb"

    # Same barcode with a new inline ingredient: nothing is created.
    r = await admin_client.post(
        "/api/v1/products",
        json={"ingredient": {"name": "Penne"}, "name": "Penne", "barcode": "024094070046"},
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "barcode_taken"
    listed = await admin_client.get("/api/v1/ingredients", params={"q": "penne"})
    assert listed.json()["items"] == []


async def test_exactly_one_ingredient_reference(admin_client, db_session):
    await seed_units_via_service(db_session)
    r = await admin_client.post("/api/v1/products", json={"name": "Orphan"})
    assert r.status_code == 422
    r = await admin_client.post(
        "/api/v1/products",
        json={
            "name": "Both",
            "ingredient_id": str(uuid.uuid4()),
            "ingredient": {"name": "X"},
        },
    )
    assert r.status_code == 422


async def test_pack_pair_enforced_by_api_and_database(
    admin_client, db_session, owner_conn: asyncpg.Connection
):
    await seed_units_via_service(db_session)
    ing = await make_ingredient(admin_client, "Olive oil", canonical_unit="ml")
    r = await admin_client.post(
        "/api/v1/products", json={"ingredient_id": ing["id"], "name": "Oil", "pack_qty": "500"}
    )
    assert r.status_code == 422
    msg = r.json()["error"]["details"]["errors"][0]["msg"]
    assert "pack_qty and pack_unit" in msg
    with pytest.raises(asyncpg.CheckViolationError):
        await owner_conn.execute(
            "INSERT INTO product (id, ingredient_id, name, pack_qty) "
            "VALUES (gen_random_uuid(), $1, 'x', 1)",
            uuid.UUID(ing["id"]),
        )
    r = await admin_client.post(
        "/api/v1/products",
        json={"ingredient_id": ing["id"], "name": "Oil", "pack_qty": "500", "pack_unit": "stone"},
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_unit"


async def test_update_clear_and_deactivate(admin_client, db_session):
    await seed_units_via_service(db_session)
    ing = await make_ingredient(admin_client, "Strawberries")
    p = await make_product(
        admin_client, ing["id"], "Stand strawberries", pack_qty="1", pack_unit="lb"
    )
    r = await admin_client.patch(
        f"/api/v1/products/{p['id']}",
        json={
            "clear_pack": True,
            "quality_rating": 5,
            "density_override": "0.6",
            "density_override_source": "measured",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pack_qty"] is None and body["pack_unit"] is None
    assert body["quality_rating"] == 5
    assert Decimal(body["density_override"]) == Decimal("0.6")
    assert body["density_override_confirmed"] is False
    r = await admin_client.post(f"/api/v1/products/{p['id']}/density-override/confirm")
    assert r.json()["density_override_confirmed"] is True
    r = await admin_client.post(f"/api/v1/products/{p['id']}/deactivate")
    assert r.json()["active"] is False
    search = await admin_client.get("/api/v1/products/search", params={"q": "strawberr"})
    assert search.json()["items"] == []
    assert (await admin_client.get(f"/api/v1/products/{p['id']}")).status_code == 200
    listed = await admin_client.get("/api/v1/products", params={"ingredient_id": ing["id"]})
    assert listed.json()["items"] == []
