"""Tidy the catalog (#349): suggestions applied only on accept. Everything is invented."""

from __future__ import annotations

from app.core.ids import new_id
from tests.catalog_helpers import (
    clear_category,
    make_ingredient,
    make_product,
    seed_units_via_service,
)
from tests.conftest import run_alembic


async def suggestions(client) -> list[dict]:
    r = await client.get("/api/v1/catalog/cleanup")
    assert r.status_code == 200, r.text
    return r.json()["items"]


def by_kind(items: list[dict], kind: str) -> list[dict]:
    return [s for s in items if s["kind"] == kind]


async def accept(client, *keys: str) -> dict:
    r = await client.post("/api/v1/catalog/cleanup/apply", json={"keys": list(keys)})
    assert r.status_code == 200, r.text
    return r.json()


async def test_a_product_name_with_brand_and_pack_is_offered_short(admin_client, db_session):
    await seed_units_via_service(db_session)
    bacon = await make_ingredient(admin_client, "lantern bacon")
    p = await make_product(
        admin_client, bacon["id"], "HARBOR LIGHT Thick Cut Bacon, 12 oz",
        brand="Harbor Light", pack_qty="12", pack_unit="oz",
    )  # fmt: skip
    [s] = by_kind(await suggestions(admin_client), "product_name")
    assert (s["subject_id"], s["after"]) == (p["id"], "Thick cut bacon")
    # Looking writes nothing.
    product = (await admin_client.get(f"/api/v1/products/{p['id']}")).json()
    assert product["name"] == "HARBOR LIGHT Thick Cut Bacon, 12 oz"
    assert (await accept(admin_client, s["key"]))["applied"] == 1
    product = (await admin_client.get(f"/api/v1/products/{p['id']}")).json()
    assert product["name"] == "Thick cut bacon"


async def test_a_category_is_offered_and_one_nobody_can_guess_waits(admin_client, db_session):
    await seed_units_via_service(db_session)
    known = await clear_category(admin_client, await make_ingredient(admin_client, "chayote"))
    unknown = await clear_category(admin_client, await make_ingredient(admin_client, "zzyzx"))
    rows = {s["subject_id"]: s for s in by_kind(await suggestions(admin_client), "category")}
    assert rows[known["id"]]["after"] == "produce" and rows[known["id"]]["ready"]
    assert not rows[unknown["id"]]["ready"]
    result = await accept(admin_client, rows[known["id"]]["key"], rows[unknown["id"]]["key"])
    assert result["applied"] == 1
    got = (await admin_client.get(f"/api/v1/ingredients/{known['id']}")).json()
    assert got["category"] == "produce"


async def test_an_ingredient_name_is_conformed_on_accept(admin_client, db_session):
    await seed_units_via_service(db_session)
    okra = await make_ingredient(admin_client, "Okra, frozen")
    [s] = by_kind(await suggestions(admin_client), "ingredient_name")
    assert s["after"] == "frozen okra"
    await accept(admin_client, s["key"])
    assert (await admin_client.get(f"/api/v1/ingredients/{okra['id']}")).json()[
        "name"
    ] == "frozen okra"


async def test_a_usda_link_that_names_another_food_is_unlinked(
    admin_client, db_session, owner_conn
):
    await seed_units_via_service(db_session)
    water = await make_ingredient(admin_client, "rice water")
    await owner_conn.execute(
        "INSERT INTO fdc_food (fdc_id, data_type, description) "
        "VALUES (900051, 'sr_legacy_food', 'Watermelon, raw')"
    )
    await owner_conn.execute(
        "INSERT INTO ingredient_ref (id, ingredient_id, system, external_id, is_preferred) "
        "VALUES ($1, $2, 'fdc', '900051', true)",
        new_id(),
        water["id"],
    )
    [s] = by_kind(await suggestions(admin_client), "usda_link")
    assert (s["before"], s["after"]) == ("Watermelon, raw", "no USDA food")
    await accept(admin_client, s["key"])
    preferred = await owner_conn.fetchval(
        "SELECT count(*) FROM ingredient_ref WHERE ingredient_id = $1 AND is_preferred", water["id"]
    )
    assert preferred == 0


async def test_duplicate_ingredients_merge_into_the_suggested_one(admin_client, db_session):
    await seed_units_via_service(db_session)
    quiet = await make_ingredient(admin_client, "lantern chilies")
    busy = await make_ingredient(admin_client, "lantern chiles")
    await make_product(admin_client, busy["id"], "Lantern chiles bag")
    [s] = by_kind(await suggestions(admin_client), "ingredient_merge")
    assert s["subject_id"] == quiet["id"]
    await accept(admin_client, s["key"])
    gone = (await admin_client.get(f"/api/v1/ingredients/{quiet['id']}")).json()
    assert gone["merged_into"] == busy["id"]


async def test_a_brand_on_a_loose_product_makes_it_branded(admin_client, db_session):
    await seed_units_via_service(db_session)
    pears = await make_ingredient(admin_client, "lantern pears")
    p = await make_product(
        admin_client, pears["id"], "Lantern pears", brand="Fernhill", kind="loose"
    )
    [s] = by_kind(await suggestions(admin_client), "product_brand")
    assert s["after"] == "branded"
    await accept(admin_client, s["key"])
    assert (await admin_client.get(f"/api/v1/products/{p['id']}")).json()["kind"] == "branded"


async def test_loose_produce_loses_its_brand_rather_than_becoming_branded(admin_client, db_session):
    await seed_units_via_service(db_session)
    quince = await make_ingredient(admin_client, "lantern quince", category="produce")
    p = await make_product(
        admin_client, quince["id"], "Lantern quince", brand="Fernhill Market", kind="loose"
    )
    [s] = by_kind(await suggestions(admin_client), "product_brand")
    assert s["after"] == "no brand, sold loose"
    await accept(admin_client, s["key"])
    got = (await admin_client.get(f"/api/v1/products/{p['id']}")).json()
    assert (got["brand"], got["kind"]) == (None, "loose")


async def test_a_brand_that_describes_the_food_is_cleared(admin_client, db_session):
    await seed_units_via_service(db_session)
    beans = await make_ingredient(admin_client, "lantern beans")
    p = await make_product(
        admin_client, beans["id"], "Organic lantern beans", brand="Organic", kind="branded"
    )
    items = await suggestions(admin_client)
    # "Organic" is not a brand, so the name keeps it.
    assert by_kind(items, "product_name") == []
    [s] = by_kind(items, "product_brand")
    assert s["after"] == "no brand"
    await accept(admin_client, s["key"])
    assert (await admin_client.get(f"/api/v1/products/{p['id']}")).json()["brand"] is None


async def test_a_real_brand_that_is_also_a_food_word_stays(admin_client, db_session):
    await seed_units_via_service(db_session)
    ale = await make_ingredient(admin_client, "lantern ale")
    await make_product(admin_client, ale["id"], "Lantern ale", brand="Stone", kind="branded")
    assert by_kind(await suggestions(admin_client), "product_brand") == []


async def test_a_frozen_food_filed_elsewhere_is_offered_frozen(admin_client, db_session):
    await seed_units_via_service(db_session)
    rolls = await make_ingredient(admin_client, "frozen lantern rolls", category="bakery")
    [s] = by_kind(await suggestions(admin_client), "category")
    assert (s["subject_id"], s["before"], s["value"]) == (rolls["id"], "bakery", "frozen")
    await accept(admin_client, s["key"])
    assert (await admin_client.get(f"/api/v1/ingredients/{rolls['id']}")).json()[
        "category"
    ] == "frozen"


async def test_a_person_chooses_a_category_instead_of_the_guess(admin_client, db_session):
    await seed_units_via_service(db_session)
    fish = await clear_category(admin_client, await make_ingredient(admin_client, "zzyzx"))
    [s] = by_kind(await suggestions(admin_client), "category")
    assert s["choosable"] and not s["ready"]
    r = await admin_client.post(
        "/api/v1/catalog/cleanup/apply", json={"keys": [s["key"]], "chosen": {s["key"]: "nope"}}
    )
    assert r.status_code == 422
    r = await admin_client.post(
        "/api/v1/catalog/cleanup/apply",
        json={"keys": [s["key"]], "chosen": {s["key"]: "seafood"}},
    )
    assert r.json()["applied"] == 1
    got = (await admin_client.get(f"/api/v1/ingredients/{fish['id']}")).json()
    assert got["category"] == "seafood"


async def test_only_a_category_can_be_chosen(admin_client, db_session):
    await seed_units_via_service(db_session)
    bacon = await make_ingredient(admin_client, "lantern bacon")
    await make_product(admin_client, bacon["id"], "Harbor Light Bacon", brand="Harbor Light")
    [s] = by_kind(await suggestions(admin_client), "product_name")
    r = await admin_client.post(
        "/api/v1/catalog/cleanup/apply", json={"keys": [s["key"]], "chosen": {s["key"]: "meat"}}
    )
    assert r.status_code == 422


async def test_a_skip_holds_until_the_data_changes(admin_client, db_session):
    await seed_units_via_service(db_session)
    bacon = await make_ingredient(admin_client, "lantern bacon")
    p = await make_product(
        admin_client, bacon["id"], "Harbor Light Bacon, 12 oz",
        brand="Harbor Light", pack_qty="12", pack_unit="oz",
    )  # fmt: skip
    [s] = by_kind(await suggestions(admin_client), "product_name")
    r = await admin_client.post("/api/v1/catalog/cleanup/skip", json={"keys": [s["key"]]})
    assert r.status_code == 204
    assert by_kind(await suggestions(admin_client), "product_name") == []
    await admin_client.patch(
        f"/api/v1/products/{p['id']}", json={"name": "Harbor Light Rashers, 12 oz"}
    )
    [again] = by_kind(await suggestions(admin_client), "product_name")
    assert again["after"] == "Rashers"


async def test_the_inbox_counts_them(admin_client, db_session):
    await seed_units_via_service(db_session)
    await make_ingredient(admin_client, "Okra, frozen", category="frozen")
    items = (await admin_client.get("/api/v1/inbox")).json()["items"]
    [row] = [i for i in items if i["kind"] == "catalog_cleanup"]
    assert row["title"] == "Tidy the catalog: 1 suggestion"
    assert row["action_route"] == "/catalog/cleanup"


def test_the_migration_applies_and_reverses():
    run_alembic("downgrade", "0055")
    run_alembic("upgrade", "head")
