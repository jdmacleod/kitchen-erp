"""USDA suggestions reviewed on Needs a bridge (03, 1G, criteria 84-85; DV6-DV8, DV18-DV21)."""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path

import asyncpg

from app.core.ids import new_id
from app.services.usda import import_usda
from tests.catalog_helpers import make_ingredient, write_usda_fixture
from tests.pricebook_helpers import make_location, make_product, shelf

FDC_DIR = "FoodData_Central_csv_2026-04-30"


async def _load(db_session, tmp_path: Path) -> None:
    write_usda_fixture(tmp_path / FDC_DIR)
    await import_usda(db_session, tmp_path / FDC_DIR)


async def _ref(owner_conn: asyncpg.Connection, ingredient_id: str, fdc_id: int) -> None:
    await owner_conn.execute(
        "INSERT INTO ingredient_ref (id, ingredient_id, system, external_id, is_preferred) "
        "VALUES ($1, $2, 'fdc', $3, true)",
        new_id(),
        uuid.UUID(ingredient_id),
        str(fdc_id),
    )


async def _review(client) -> dict:
    r = await client.get("/api/v1/usda/review")
    assert r.status_code == 200, r.text
    return r.json()


def _group(review: dict, name: str) -> dict | None:
    return next((g for g in review["groups"] if g["name"] == name), None)


async def test_without_usda_data_nothing_is_offered(admin_client, owner_conn):
    flour = await make_ingredient(admin_client, "Flour")
    await _ref(owner_conn, flour["id"], 1001)
    assert await _review(admin_client) == {"loaded": False, "release_date": None, "groups": []}
    inbox = (await admin_client.get("/api/v1/inbox")).json()["items"]
    assert not [i for i in inbox if i["kind"] == "usda"]


async def test_offers_densities_and_measures_the_ingredient_lacks(
    admin_client, db_session, owner_conn, tmp_path
):
    await _load(db_session, tmp_path)
    flour = await make_ingredient(admin_client, "Flour")
    garlic = await make_ingredient(admin_client, "Garlic")
    oil = await make_ingredient(admin_client, "Milk", canonical_unit="ml")
    await make_ingredient(admin_client, "Sorrel")  # no reference: never listed
    for ing, fdc in ((flour, 1001), (garlic, 1002), (oil, 1004)):
        await _ref(owner_conn, ing["id"], fdc)
    review = await _review(admin_client)
    assert review["loaded"] is True and review["release_date"] == "2026-04-30"
    assert [g["name"] for g in review["groups"]] == ["Flour", "Garlic", "Milk"]
    f = _group(review, "Flour")
    assert f["usda_description"] == "Flour, wheat, white, all-purpose, enriched"
    assert [d["portion_label"] for d in f["densities"]] == ["1 cup", "1 tablespoon"]
    assert Decimal(f["densities"][0]["density_g_per_ml"]) == Decimal("0.52834")
    g = _group(review, "Garlic")
    assert [(m["label"], m["canonical_qty"]) for m in g["measures"]] == [("clove", "3")]
    # An ml ingredient gets densities only.
    assert _group(review, "Milk")["measures"] == []

    # DV19: what the ingredient already has is left out.
    await admin_client.patch(
        f"/api/v1/ingredients/{flour['id']}",
        json={"density_g_per_ml": "0.53", "density_source": "measured"},
    )
    await admin_client.post(
        f"/api/v1/ingredients/{garlic['id']}/measures",
        json={"label": "Clove", "canonical_qty": "4", "source": "measured"},
    )
    review = await _review(admin_client)
    assert _group(review, "Flour") is None  # nothing left to offer
    assert _group(review, "Garlic")["measures"] == []


async def test_an_each_ingredient_gets_measures_only_and_only_with_a_medium(
    admin_client, db_session, owner_conn, tmp_path
):
    await _load(db_session, tmp_path)
    garlic = await make_ingredient(admin_client, "Garlic head", canonical_unit="each")
    await _ref(owner_conn, garlic["id"], 1002)
    assert _group(await _review(admin_client), "Garlic head") is None  # DV21
    await owner_conn.execute(
        "INSERT INTO ref_usda_portion (id, fdc_id, food_description, portion_label, "
        "portion_amount, portion_unit, gram_weight, data_type) "
        "VALUES ($1, 1002, 'Garlic, raw', '1 medium', 1, 'medium', 6, 'sr_legacy_food')",
        new_id(),
    )
    g = _group(await _review(admin_client), "Garlic head")
    assert g["densities"] == []
    assert {m["label"]: m["canonical_qty"] for m in g["measures"]} == {
        "clove": "0.5",
        "teaspoon": "0.467",
    }


async def test_accepting_saves_unconfirmed_usda_values_and_recomputes(
    admin_client, db_session, owner_conn, tmp_path
):
    await _load(db_session, tmp_path)
    loc = await make_location(admin_client, "Hilltop Stand", "Hilltop Stand")
    product = await make_product(admin_client, "Flour", "Bulk flour")
    flour = product["ingredient"]
    by_cup = await shelf(admin_client, product["id"], loc["id"], "0.89", qty="1", unit="cup")
    assert by_cup["norm"]["status"] == "no_density"
    await _ref(owner_conn, flour["id"], 1001)
    [cup, _tbsp] = _group(await _review(admin_client), "Flour")["densities"]

    r = await admin_client.post(
        f"/api/v1/usda/review/{flour['id']}", json={"density_portion_id": cup["portion_id"]}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["saved"] == 1
    assert body["ingredient"]["density_source"] == "usda"
    assert body["ingredient"]["density_confirmed"] is False
    assert Decimal(body["ingredient"]["density_g_per_ml"]) == Decimal("0.52834")
    after = (await admin_client.get(f"/api/v1/price-observations/{by_cup['id']}")).json()
    assert after["norm"]["status"] == "ok" and after["norm"]["bridge_kind"] == "density"
    assert _group(await _review(admin_client), "Flour") is None


async def test_a_density_set_since_reading_is_a_409_then_replace(
    admin_client, db_session, owner_conn, tmp_path
):
    await _load(db_session, tmp_path)
    garlic = await make_ingredient(admin_client, "Garlic")
    await _ref(owner_conn, garlic["id"], 1002)
    group = _group(await _review(admin_client), "Garlic")
    [tsp] = group["densities"]
    await admin_client.patch(
        f"/api/v1/ingredients/{garlic['id']}",
        json={"density_g_per_ml": "0.52", "density_source": "measured"},
    )
    body = {"density_portion_id": tsp["portion_id"], "measures": ["clove"]}
    r = await admin_client.post(f"/api/v1/usda/review/{garlic['id']}", json=body)
    assert r.status_code == 409
    error = r.json()["error"]
    assert error["code"] == "density_exists"
    assert error["details"] == {
        "density_g_per_ml": "0.52000",
        "density_source": "measured",
        "density_confirmed": False,
    }
    # Keep it: send the measures alone.
    r = await admin_client.post(f"/api/v1/usda/review/{garlic['id']}", json={"measures": ["clove"]})
    assert r.status_code == 200 and r.json()["saved"] == 1
    assert r.json()["ingredient"]["density_source"] == "measured"
    measures = r.json()["ingredient"]["measures"]
    assert [(m["label"], m["source"], m["confirmed"]) for m in measures] == [
        ("clove", "usda", False)
    ]


async def test_replace_takes_the_usda_density(admin_client, db_session, owner_conn, tmp_path):
    await _load(db_session, tmp_path)
    garlic = await make_ingredient(admin_client, "Garlic")
    await _ref(owner_conn, garlic["id"], 1002)
    [tsp] = _group(await _review(admin_client), "Garlic")["densities"]
    await admin_client.patch(
        f"/api/v1/ingredients/{garlic['id']}",
        json={"density_g_per_ml": "0.52", "density_source": "measured"},
    )
    r = await admin_client.post(
        f"/api/v1/usda/review/{garlic['id']}",
        json={"density_portion_id": tsp["portion_id"], "replace_density": True},
    )
    assert r.status_code == 200 and r.json()["ingredient"]["density_source"] == "usda"


async def test_skip_saves_nothing_and_leaves_the_list(
    admin_client, db_session, owner_conn, tmp_path
):
    await _load(db_session, tmp_path)
    garlic = await make_ingredient(admin_client, "Garlic")
    await _ref(owner_conn, garlic["id"], 1002)
    r = await admin_client.post(f"/api/v1/usda/review/{garlic['id']}", json={"skip": True})
    assert r.status_code == 200 and r.json()["saved"] == 0
    assert r.json()["ingredient"]["measures"] == []
    assert _group(await _review(admin_client), "Garlic") is None


async def test_the_inbox_row_waits_for_linking_unless_a_linked_ingredient_has_suggestions(
    admin_client, db_session, owner_conn, tmp_path
):
    await _load(db_session, tmp_path)
    garlic = await make_ingredient(admin_client, "Garlic")
    other = await make_ingredient(admin_client, "Sorrel")
    await _ref(owner_conn, garlic["id"], 1002)

    def usda_rows(items):
        return [i for i in items if i["kind"] == "usda"]

    [row] = usda_rows((await admin_client.get("/api/v1/inbox")).json()["items"])
    assert row["title"] == "1 ingredient has USDA densities to review"
    assert row["action_route"] == "/catalog/bridges#usda"
    await owner_conn.execute(
        "UPDATE ingredient SET reconcile_state = 'unreviewed' WHERE id = $1",
        uuid.UUID(other["id"]),
    )
    assert usda_rows((await admin_client.get("/api/v1/inbox")).json()["items"]) == []
    await owner_conn.execute(
        "UPDATE ingredient SET reconcile_state = 'linked' WHERE id = $1",
        uuid.UUID(garlic["id"]),
    )
    assert len(usda_rows((await admin_client.get("/api/v1/inbox")).json()["items"])) == 1
