"""Possible duplicate ingredients (#346). Every ingredient and USDA food is invented."""

from __future__ import annotations

from app.core.ids import new_id
from app.services.ingredient_duplicates import _similar, name_key
from tests.catalog_helpers import make_ingredient, make_product, seed_units_via_service
from tests.conftest import run_alembic


async def pairs(client) -> list[tuple[set[str], list[str]]]:
    r = await client.get("/api/v1/ingredients/duplicates")
    assert r.status_code == 200, r.text
    return [({p["a"]["name"], p["b"]["name"]}, p["reasons"]) for p in r.json()["items"]]


def test_a_name_key_folds_order_plurals_and_spellings():
    assert name_key("Okra, frozen") == name_key("frozen okra")
    assert name_key("red chilies") == name_key("red chiles") == name_key("red chillies")


def test_only_a_spelling_slip_reads_as_a_similar_name():
    assert _similar("worcestershire sauce", "worchestershire sauce")
    assert not _similar("red wine vinegar", "white wine vinegar")
    assert not _similar("onion", "red onion")


async def test_two_ingredients_on_one_fitting_usda_food_are_offered(
    admin_client, db_session, owner_conn
):
    await seed_units_via_service(db_session)
    a = await make_ingredient(admin_client, "lantern root")
    b = await make_ingredient(admin_client, "harbor lantern root")
    c = await make_ingredient(admin_client, "lantern powder")
    await owner_conn.execute(
        "INSERT INTO fdc_food (fdc_id, data_type, description) "
        "VALUES (900041, 'sr_legacy_food', 'Lantern root, raw')"
    )
    insert = (
        "INSERT INTO ingredient_ref (id, ingredient_id, system, external_id, is_preferred) "
        "VALUES ($1, $2, 'fdc', '900041', true)"
    )
    for ingredient in (a, b, c):
        await owner_conn.execute(insert, new_id(), ingredient["id"])
    # The powder shares the record but isn't its food: a wrong link pairs nothing.
    assert await pairs(admin_client) == [
        ({"lantern root", "harbor lantern root"}, ["same USDA food"])
    ]


async def test_the_same_name_in_another_order_or_spelling_is_offered(admin_client, db_session):
    await seed_units_via_service(db_session)
    await make_ingredient(admin_client, "frozen okra")
    await make_ingredient(admin_client, "okra, frozen")
    await make_ingredient(admin_client, "red onion")
    await make_ingredient(admin_client, "onion")
    assert await pairs(admin_client) == [({"frozen okra", "okra, frozen"}, ["same name"])]


async def test_not_the_same_is_remembered(admin_client, db_session):
    await seed_units_via_service(db_session)
    a = await make_ingredient(admin_client, "lantern chilies")
    b = await make_ingredient(admin_client, "lantern chiles")
    r = await admin_client.post(
        "/api/v1/ingredients/duplicates/distinct", json={"a": b["id"], "b": a["id"]}
    )
    assert r.status_code == 204
    assert await pairs(admin_client) == []
    r = await admin_client.post(
        "/api/v1/ingredients/duplicates/distinct", json={"a": a["id"], "b": a["id"]}
    )
    assert r.status_code == 422


async def test_the_one_with_more_products_is_suggested_to_keep(admin_client, db_session):
    await seed_units_via_service(db_session)
    quiet = await make_ingredient(admin_client, "lantern chilies")
    busy = await make_ingredient(admin_client, "lantern chiles")
    await make_product(admin_client, busy["id"], "Harbor Light lantern chiles")
    [pair] = (await admin_client.get("/api/v1/ingredients/duplicates")).json()["items"]
    assert pair["keep_id"] == busy["id"] and quiet["id"] in (pair["a"]["id"], pair["b"]["id"])


async def test_the_inbox_has_a_row_for_them(admin_client, db_session):
    await seed_units_via_service(db_session)
    await make_ingredient(admin_client, "lantern chilies")
    await make_ingredient(admin_client, "lantern chiles")
    items = (await admin_client.get("/api/v1/inbox")).json()["items"]
    [row] = [i for i in items if i["kind"] == "ingredient_duplicates"]
    assert row["title"] == "1 possible duplicate ingredient"
    assert row["action_route"] == "/catalog/ingredients/link#duplicates"


def test_the_migration_applies_and_reverses():
    run_alembic("downgrade", "0054")
    run_alembic("upgrade", "head")
