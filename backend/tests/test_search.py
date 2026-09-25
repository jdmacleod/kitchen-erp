"""GET /search, the search palette's endpoint (UI-2.8). All names are invented."""

import httpx
import pytest

from tests.catalog_helpers import make_ingredient, make_product, seed_units_via_service
from tests.pricebook_helpers import make_location

# GS1 prefix 2 is for restricted circulation, so no retail product carries it.
BARCODE = "200000000424"


async def _search(client: httpx.AsyncClient, q: str) -> dict:
    r = await client.get("/api/v1/search", params={"q": q})
    assert r.status_code == 200, r.text
    return r.json()


async def test_results_are_grouped_with_labels_and_routes(admin_client, db_session):
    await seed_units_via_service(db_session)
    basil = await make_ingredient(admin_client, "Basil", category="Herbs")
    product = await make_product(admin_client, basil["id"], "Basil bunch", brand="Leafwise")
    stand = await make_location(admin_client, "Basil Barn", "Basil Barn", kind="stand")
    await make_ingredient(admin_client, "Paprika", category="spice")

    body = await _search(admin_client, "basil")
    assert list(body) == ["ingredients", "products", "vendors"]
    assert body["ingredients"] == [
        {
            "kind": "ingredient",
            "id": basil["id"],
            "label": "Basil",
            "detail": "Herbs",
            "route": f"/catalog/ingredients/{basil['id']}",
            "category_key": "produce",
        }
    ]
    assert body["products"] == [
        {
            "kind": "product",
            "id": product["id"],
            "label": "Basil bunch",
            "detail": "Leafwise · Basil",
            "route": f"/catalog/products/{product['id']}",
            "category_key": "produce",
        }
    ]
    assert body["vendors"] == [
        {
            "kind": "vendor",
            "id": stand["vendor"]["id"],
            "label": "Basil Barn",
            "detail": "Stand",
            "route": f"/catalog/vendors/{stand['vendor']['id']}",
            "category_key": None,
        }
    ]


async def test_a_barcode_matches_its_product_exactly_and_first(admin_client, db_session):
    await seed_units_via_service(db_session)
    rice = await make_ingredient(admin_client, "Rice")
    wanted = await make_product(admin_client, rice["id"], "Short grain rice", barcode=BARCODE)
    await make_product(admin_client, rice["id"], f"Rice {BARCODE} lookalike")

    body = await _search(admin_client, BARCODE)
    assert body["products"][0]["id"] == wanted["id"]
    assert body["ingredients"] == [] and body["vendors"] == []


async def test_each_group_is_capped_and_inactive_items_are_left_out(admin_client, db_session):
    await seed_units_via_service(db_session)
    for i in range(10):
        await make_ingredient(admin_client, f"Chili variety {i}")
    gone = await make_ingredient(admin_client, "Chili retired")
    r = await admin_client.post(f"/api/v1/ingredients/{gone['id']}/deactivate")
    assert r.status_code == 200, r.text

    body = await _search(admin_client, "chili")
    assert len(body["ingredients"]) == 8
    assert gone["id"] not in {i["id"] for i in body["ingredients"]}


async def test_nothing_found_is_three_empty_groups(admin_client):
    assert await _search(admin_client, "   ") == {"ingredients": [], "products": [], "vendors": []}
    assert await _search(admin_client, "zzqx") == {"ingredients": [], "products": [], "vendors": []}


@pytest.mark.parametrize("q", ["", "x" * 201])
async def test_q_must_be_1_to_200_characters(admin_client, q):
    r = await admin_client.get("/api/v1/search", params={"q": q})
    assert r.status_code == 422


async def test_search_needs_a_session(client):
    r = await client.get("/api/v1/search", params={"q": "basil"})
    assert r.status_code == 401
