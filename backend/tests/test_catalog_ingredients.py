from decimal import Decimal

import httpx

from tests.catalog_helpers import make_ingredient, make_product, seed_units_via_service


async def test_create_with_only_a_name_and_use_it(admin_client: httpx.AsyncClient, db_session):
    await seed_units_via_service(db_session)
    ing = await make_ingredient(admin_client, "Dry rigatoni")
    assert ing["canonical_unit"] == "g"
    assert ing["yield_pct"] == "1"
    assert ing["perishability"] == "shelf_stable"
    assert ing["density_g_per_ml"] is None
    product = await make_product(admin_client, ing["id"], "Rigatoni 1 lb box")
    assert product["ingredient"]["id"] == ing["id"]


async def test_name_unique_case_insensitively_with_specific_code(admin_client, db_session):
    await seed_units_via_service(db_session)
    await make_ingredient(admin_client, "Kosher Salt")
    r = await admin_client.post("/api/v1/ingredients", json={"name": "kosher salt"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "ingredient_name_taken"


async def test_density_pair_validation(admin_client, db_session):
    await seed_units_via_service(db_session)
    r = await admin_client.post(
        "/api/v1/ingredients", json={"name": "Honey", "density_g_per_ml": "1.42"}
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
    assert "together" in r.json()["error"]["details"]["errors"][0]["msg"]


async def test_density_set_then_confirmed_as_distinct_action(admin_client, db_session):
    await seed_units_via_service(db_session)
    ing = await make_ingredient(
        admin_client, "Honey", density_g_per_ml="1.42", density_source="usda"
    )
    assert ing["density_confirmed"] is False
    r = await admin_client.post(f"/api/v1/ingredients/{ing['id']}/density/confirm")
    assert r.status_code == 200 and r.json()["density_confirmed"] is True
    # A new value is unconfirmed again.
    r = await admin_client.patch(
        f"/api/v1/ingredients/{ing['id']}",
        json={"density_g_per_ml": "1.40", "density_source": "measured"},
    )
    assert r.json()["density_confirmed"] is False
    assert Decimal(r.json()["density_g_per_ml"]) == Decimal("1.40")
    r = await admin_client.patch(f"/api/v1/ingredients/{ing['id']}", json={"clear_density": True})
    assert r.json()["density_g_per_ml"] is None and r.json()["density_source"] is None
    r = await admin_client.post(f"/api/v1/ingredients/{ing['id']}/density/confirm")
    assert r.status_code == 409


async def test_measures_crud_and_confirm(admin_client, db_session):
    await seed_units_via_service(db_session)
    ing = await make_ingredient(admin_client, "Garlic")
    r = await admin_client.post(
        f"/api/v1/ingredients/{ing['id']}/measures",
        json={"label": "clove", "canonical_qty": "5", "source": "usda"},
    )
    assert r.status_code == 201, r.text
    measure = r.json()
    assert measure["confirmed"] is False and measure["canonical_qty"] == "5"
    dup = await admin_client.post(
        f"/api/v1/ingredients/{ing['id']}/measures",
        json={"label": "Clove", "canonical_qty": "6", "source": "manual"},
    )
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "measure_label_taken"
    r = await admin_client.post(f"/api/v1/measures/{measure['id']}/confirm")
    assert r.json()["confirmed"] is True
    r = await admin_client.patch(f"/api/v1/measures/{measure['id']}", json={"canonical_qty": "4.5"})
    assert r.json()["confirmed"] is False and r.json()["canonical_qty"] == "4.5"
    detail = await admin_client.get(f"/api/v1/ingredients/{ing['id']}")
    assert [m["label"] for m in detail.json()["measures"]] == ["clove"]
    r = await admin_client.delete(f"/api/v1/measures/{measure['id']}")
    assert r.status_code == 204
    detail = await admin_client.get(f"/api/v1/ingredients/{ing['id']}")
    assert detail.json()["measures"] == []


async def test_deactivated_hidden_from_list_but_resolvable(admin_client, db_session):
    await seed_units_via_service(db_session)
    ing = await make_ingredient(admin_client, "Saffron")
    r = await admin_client.post(f"/api/v1/ingredients/{ing['id']}/deactivate")
    assert r.json()["active"] is False
    listed = await admin_client.get("/api/v1/ingredients")
    assert ing["id"] not in [i["id"] for i in listed.json()["items"]]
    listed = await admin_client.get("/api/v1/ingredients", params={"include_inactive": "true"})
    assert ing["id"] in [i["id"] for i in listed.json()["items"]]
    assert (await admin_client.get(f"/api/v1/ingredients/{ing['id']}")).status_code == 200


async def test_list_pagination_cursor(admin_client, db_session):
    await seed_units_via_service(db_session)
    for i in range(5):
        await make_ingredient(admin_client, f"Item {i}")
    page1 = (await admin_client.get("/api/v1/ingredients", params={"limit": 2})).json()
    assert len(page1["items"]) == 2 and page1["next_cursor"]
    page2 = (
        await admin_client.get(
            "/api/v1/ingredients", params={"limit": 2, "cursor": page1["next_cursor"]}
        )
    ).json()
    assert len(page2["items"]) == 2
    page3 = (
        await admin_client.get(
            "/api/v1/ingredients", params={"limit": 2, "cursor": page2["next_cursor"]}
        )
    ).json()
    assert len(page3["items"]) == 1 and page3["next_cursor"] is None
    names = [i["name"] for i in page1["items"] + page2["items"] + page3["items"]]
    assert sorted(names) == [f"Item {i}" for i in range(5)]


async def test_idempotent_create(admin_client, db_session):
    await seed_units_via_service(db_session)
    h = {"Idempotency-Key": "ing-1"}
    a = await admin_client.post("/api/v1/ingredients", json={"name": "Cumin"}, headers=h)
    b = await admin_client.post("/api/v1/ingredients", json={"name": "Cumin"}, headers=h)
    assert a.status_code == b.status_code == 201 and a.json() == b.json()
