"""Unconfirmed bridges: one list of every unchecked value, and its inbox row (issue 356)."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import event

from app.catalog.bridge_check import density_problem, read_measure
from app.services import inbox, unconfirmed_bridges
from app.units import units_by_code
from tests.catalog_helpers import make_ingredient, make_product

U = units_by_code()


def _problem(label: str, qty: str, unit: str = "g") -> str | None:
    return read_measure(label, Decimal(qty), unit, U).problem


def test_the_check_matches_the_frontend_cases():
    """The same cases as frontend/src/test/measure-check.test.ts."""
    assert _problem("cup", "125") is None
    assert _problem("cup, sliced", "150") is None
    assert _problem("oz, boneless", "28.35") is None
    assert "lighter than any food" in (_problem("cup juice", "0.33") or "")
    assert "heavier than any food" in (_problem("cups", "2000") or "")
    assert _problem("lb", "100") == "One lb at 100 g doesn't match 1 lb (454 g)."
    assert _problem("clove", "0.1") is None
    assert _problem("cup", "1", "ml") is None
    assert _problem("cup", "1", "each") is None


def test_a_volume_measure_says_the_density_it_implies():
    assert read_measure("cup", Decimal("125"), "g", U).g_per_ml == Decimal("0.528")
    assert read_measure("clove", Decimal("3"), "g", U).g_per_ml is None


def test_densities_outside_any_food_are_flagged():
    assert density_problem(Decimal("0.52")) is None
    assert density_problem(Decimal("0.0123")) == "0.012 g/ml is lighter than any food."
    assert density_problem(Decimal("31")) == "31 g/ml is heavier than any food."


async def _measure(client, ingredient_id: str, label: str, qty: str, source: str = "usda") -> dict:
    r = await client.post(
        f"/api/v1/ingredients/{ingredient_id}/measures",
        json={"label": label, "canonical_qty": qty, "source": source},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _list(client) -> list[dict]:
    r = await client.get("/api/v1/bridges/unconfirmed")
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def test_every_unconfirmed_value_is_listed_with_the_implausible_first(admin_client):
    bread = await make_ingredient(admin_client, "Tarn bread flour")
    honey = await make_ingredient(
        admin_client, "Honey", density_g_per_ml="1.42", density_source="usda"
    )
    lemons = await make_ingredient(admin_client, "Lemons")
    await _measure(admin_client, bread["id"], "cup", "125")
    await _measure(admin_client, lemons["id"], "tbsp juice", "0.33")  # per lemon, typed backwards
    checked = await _measure(admin_client, bread["id"], "slice", "25", source="measured")
    r = await admin_client.post(f"/api/v1/measures/{checked['id']}/confirm")
    assert r.status_code == 200, r.text
    product = await make_product(
        admin_client,
        honey["id"],
        "Clover honey",
        density_override="1.38",
        density_override_source="label",
    )
    gone = await make_ingredient(
        admin_client, "Retired syrup", density_g_per_ml="1.3", density_source="usda"
    )
    r = await admin_client.post(f"/api/v1/ingredients/{gone['id']}/deactivate")
    assert r.status_code == 200, r.text

    items = await _list(admin_client)
    assert [(v["kind"], v["ingredient_name"], v["label"] or v["product_name"]) for v in items] == [
        ("measure", "Lemons", "tbsp juice"),
        ("density", "Honey", None),
        ("product_density", "Honey", "Clover honey"),
        ("measure", "Tarn bread flour", "cup"),
    ]
    assert items[0]["problem"].startswith("One tbsp juice at 0.33 g would be 0.022 g/ml")
    assert items[0]["g_per_ml"] == "0.0223"
    assert items[3] | {"id": None} == items[3] | {
        "id": None,
        "value": "125",
        "source": "usda",
        "g_per_ml": "0.528",
        "problem": None,
    }
    assert items[2]["id"] == product["id"]

    # Confirming takes a value off the list.
    r = await admin_client.post(f"/api/v1/ingredients/{honey['id']}/density/confirm")
    assert r.status_code == 200, r.text
    assert ("density", "Honey") not in [
        (v["kind"], v["ingredient_name"]) for v in await _list(admin_client)
    ]


async def test_the_inbox_row_counts_them_and_leaves_when_all_are_checked(admin_client, db_session):
    salt = await make_ingredient(admin_client, "Sea salt")
    assert await inbox._unconfirmed_bridges(db_session) == []
    a = await _measure(admin_client, salt["id"], "tsp", "6")
    b = await _measure(admin_client, salt["id"], "pinch", "0.4")
    [row] = await inbox._unconfirmed_bridges(db_session)
    assert row.title == "2 bridge values wait for a check"
    assert row.action_route == "/catalog/bridges#unconfirmed"
    await admin_client.post(f"/api/v1/measures/{a['id']}/confirm")
    [row] = await inbox._unconfirmed_bridges(db_session)
    assert row.title == "1 bridge value waits for a check"
    r = await admin_client.delete(f"/api/v1/measures/{b['id']}")
    assert r.status_code == 204
    assert await inbox._unconfirmed_bridges(db_session) == []


async def test_the_inbox_row_costs_three_queries_however_many_values(admin_client, db_session):
    async def queries() -> int:
        count = 0

        def tick(*_):
            nonlocal count
            count += 1

        engine = db_session.bind.sync_engine
        event.listen(engine, "before_cursor_execute", tick)
        try:
            await unconfirmed_bridges.waiting(db_session)
        finally:
            event.remove(engine, "before_cursor_execute", tick)
        return count

    salt = await make_ingredient(admin_client, "Sea salt")
    await _measure(admin_client, salt["id"], "tsp", "6")
    few = await queries()
    for i in range(20):
        await _measure(admin_client, salt["id"], f"spoon {i}", "5")
    assert await queries() == few == 3
    assert (await unconfirmed_bridges.waiting(db_session))[0] == 21
