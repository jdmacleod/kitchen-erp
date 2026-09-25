"""GET /products: name order, keyset pages, server-side search and category filter (D12),
category keys on ingredient shapes, and last_paid (T16). All names are invented."""

import httpx
import pytest

from tests.catalog_helpers import make_ingredient, make_product, seed_units_via_service


async def _names(client: httpx.AsyncClient, **params) -> tuple[list[str], str | None]:
    r = await client.get("/api/v1/products", params=params)
    assert r.status_code == 200, r.text
    body = r.json()
    return [p["name"] for p in body["items"]], body["next_cursor"]


async def _all_pages(client: httpx.AsyncClient, **params) -> list[list[str]]:
    pages, cursor = [], None
    while True:
        names, cursor = await _names(client, **params, **({"cursor": cursor} if cursor else {}))
        pages.append(names)
        if cursor is None:
            return pages


async def test_products_are_ordered_by_name_across_pages(admin_client, db_session):
    await seed_units_via_service(db_session)
    grain = await make_ingredient(admin_client, "Oats", category="Grains")
    # Created out of order, with mixed case and a tie on lower(name).
    for name in ["rolled oats", "Oat bran", "Steel-cut oats", "oat bran", "Instant oats"]:
        await make_product(admin_client, grain["id"], name)

    pages = await _all_pages(admin_client, limit=2)
    assert [len(p) for p in pages] == [2, 2, 1]
    flat = [n for p in pages for n in p]
    assert [n.lower() for n in flat] == sorted(n.lower() for n in flat)
    assert sorted(flat) == sorted(
        ["rolled oats", "Oat bran", "Steel-cut oats", "oat bran", "Instant oats"]
    )


async def test_a_category_filter_finds_a_product_beyond_the_first_page(admin_client, db_session):
    await seed_units_via_service(db_session)
    pantry = await make_ingredient(admin_client, "Lentils", category="dry goods")
    dairy = await make_ingredient(admin_client, "Butter", category=" Dairy ")
    for i in range(5):
        await make_product(admin_client, pantry["id"], f"A lentil pack {i}")
    await make_product(admin_client, dairy["id"], "Z cultured butter")

    names, cursor = await _names(admin_client, limit=3)
    assert "Z cultured butter" not in names and cursor is not None

    names, cursor = await _names(admin_client, limit=3, category="dairy")
    assert names == ["Z cultured butter"] and cursor is None
    pantry_pages = await _all_pages(admin_client, limit=3, category="pantry")
    assert [len(p) for p in pantry_pages] == [3, 2]

    # A key no stored category maps to is an empty list, not an error.
    assert await _names(admin_client, category="frozen") == ([], None)


async def test_search_runs_on_the_server_and_combines_with_the_category(admin_client, db_session):
    await seed_units_via_service(db_session)
    pantry = await make_ingredient(admin_client, "Rolled oats", category="grains")
    dairy = await make_ingredient(admin_client, "Oat milk", category="milk")
    for i in range(4):
        await make_product(admin_client, pantry["id"], f"Bulk rolled oats {i}")
    await make_product(admin_client, dairy["id"], "Barista oat milk", brand="Meadowfield")

    names, cursor = await _names(admin_client, q="oat", limit=10)
    assert len(names) == 5 and cursor is None
    names, _ = await _names(admin_client, q="oat", category="dairy")
    assert names == ["Barista oat milk"]
    names, _ = await _names(admin_client, q="meadowfield")
    assert names == ["Barista oat milk"]


@pytest.mark.parametrize(
    "params",
    [{"q": ""}, {"q": "x" * 201}, {"category": "pasta"}, {"category": "Dairy"}],
)
async def test_bad_filters_are_rejected(admin_client, params):
    r = await admin_client.get("/api/v1/products", params=params)
    assert r.status_code == 422


async def test_a_cursor_for_no_product_is_rejected(admin_client):
    r = await admin_client.get("/api/v1/products", params={"cursor": "AAAAAAAAAAAAAAAAAAAAAA"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_cursor"


async def test_ingredient_shapes_carry_the_category_key(admin_client, db_session):
    await seed_units_via_service(db_session)
    fish = await make_ingredient(admin_client, "Hake", category="Fish")
    plain = await make_ingredient(admin_client, "Tofu")
    odd = await make_ingredient(admin_client, "Kombu", category="sea vegetables")
    assert fish["category_key"] == "seafood" and fish["category"] == "Fish"
    assert plain["category_key"] is None and odd["category_key"] is None

    product = await make_product(admin_client, fish["id"], "Hake fillets")
    assert product["ingredient"]["category"] == "Fish"
    assert product["ingredient"]["category_key"] == "seafood"
    listed = (await admin_client.get("/api/v1/ingredients")).json()["items"]
    assert {i["name"]: i["category_key"] for i in listed} == {
        "Hake": "seafood",
        "Tofu": None,
        "Kombu": None,
    }
    hits = (await admin_client.get("/api/v1/products/search", params={"q": "hake"})).json()
    assert hits["items"][0]["ingredient"]["category_key"] == "seafood"


async def test_a_rename_between_pages_does_not_move_the_boundary(admin_client, db_session):
    await seed_units_via_service(db_session)
    grain = await make_ingredient(admin_client, "Millet")
    made = {}
    for name in ["Bread", "Corn", "Dates", "Eggs"]:
        made[name] = await make_product(admin_client, grain["id"], name)

    names, cursor = await _names(admin_client, limit=2)
    assert names == ["Bread", "Corn"]
    # The row the cursor points past is renamed to sort last before page two loads.
    r = await admin_client.patch(
        f"/api/v1/products/{made['Corn']['id']}", json={"name": "Zucchini"}
    )
    assert r.status_code == 200, r.text

    names, _ = await _names(admin_client, limit=2, cursor=cursor)
    assert names == ["Dates", "Eggs"]


async def test_percent_and_underscore_match_literally(admin_client, db_session):
    await seed_units_via_service(db_session)
    oats = await make_ingredient(admin_client, "Oats")
    await make_ingredient(admin_client, "Rice_flour")
    await make_product(admin_client, oats["id"], "Rolled oats")
    await make_product(admin_client, oats["id"], "Oats 100% whole")

    names, _ = await _names(admin_client, q="%")
    assert names == ["Oats 100% whole"]
    listed = (await admin_client.get("/api/v1/ingredients", params={"q": "_"})).json()["items"]
    assert [i["name"] for i in listed] == ["Rice_flour"]
    found = (await admin_client.get("/api/v1/search", params={"q": "_"})).json()
    assert [i["label"] for i in found["ingredients"]] == ["Rice_flour"]
