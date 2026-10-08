"""Naming new products in bulk from the to-identify queue (04, 2I; #88)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.services import resolution
from app.services.naming import name_from_words, read_pack
from tests.catalog_helpers import make_ingredient, seed_units_via_service
from tests.pricebook_helpers import make_location
from tests.resolution_helpers import make_receipt_purchase


@pytest.mark.parametrize(
    ("norm", "pack", "name"),
    [
        ("RVRBND BREAD FLR 2KG", ("2", "kg"), "Rvrbnd bread flr"),
        ("EGGS LG 12CT", ("12", "each"), "Eggs lg"),
        ("CLOVER HONEY 16 OZ", ("16", "oz"), "Clover honey"),
        ("OAT MILK 1.5L", ("1.5", "l"), "Oat milk"),
        ("PAPER TOWELS 6RL", None, "Paper towels 6rl"),  # rolls: not a unit
        ("GRK YOGURT PLN 5.3Z", None, "Grk yogurt pln 5.3z"),
        ("EGGS 12", None, "Eggs 12"),  # a bare number is not a size
        ("WT BROCCOLI CROWNS", None, "Wt broccoli crowns"),
    ],
)
def test_the_wording_gives_a_name_and_a_pack(norm, pack, name):
    found, rest = read_pack(norm.split())
    assert (None if found is None else (str(found.qty), found.unit)) == pack
    assert name_from_words(rest) == name


async def _committed(client, admin, db_session, loc, lines) -> str:
    pid = await make_receipt_purchase(admin.id, loc["id"], lines)
    await resolution.resolve_purchase(db_session, uuid.UUID(pid))
    r = await client.post(f"/api/v1/purchases/{pid}/commit")
    assert r.status_code == 200, r.text
    return pid


async def _queue(client, admin, db_session) -> dict:
    await seed_units_via_service(db_session)
    loc = await make_location(client, "Quayside Grocer", "Quayside Grocer North")
    await _committed(
        client,
        admin,
        db_session,
        loc,
        [
            {"raw_text": "WT BROCCOLI CROWNS 4.12 F", "line_total": "4.12"},
            {"raw_text": "RVRBND BREAD FLR 2KG 6.50", "line_total": "6.50"},
            {"raw_text": "BNLS CHKN BRST 8.40", "line_total": "8.40"},
        ],
    )
    await _committed(
        client,
        admin,
        db_session,
        loc,
        [{"raw_text": "WT BROCCOLI CROWNS 3.90 F", "line_total": "3.90"}],
    )
    return loc


async def test_every_waiting_group_is_a_row_with_suggestions(admin_client, admin, db_session):
    await _queue(admin_client, admin, db_session)
    await make_ingredient(admin_client, "broccoli")
    r = await admin_client.get("/api/v1/to-identify/naming")
    assert r.status_code == 200, r.text
    rows = {row["raw_text_norm"]: row for row in r.json()["items"]}
    assert set(rows) == {"WT BROCCOLI CROWNS", "RVRBND BREAD FLR 2KG", "BNLS CHKN BRST"}
    broccoli = rows["WT BROCCOLI CROWNS"]
    assert (broccoli["line_count"], broccoli["name"]) == (2, "Wt broccoli crowns")
    assert (broccoli["ingredient"]["kind"], broccoli["ingredient"]["name"]) == (
        "ingredient",
        "broccoli",
    )
    flour = rows["RVRBND BREAD FLR 2KG"]
    assert (flour["name"], flour["pack_qty"], flour["pack_unit"]) == ("Rvrbnd bread flr", "2", "kg")
    chicken = rows["BNLS CHKN BRST"]
    assert chicken["ingredient"] is None and chicken["pack_qty"] is None


async def test_confirmed_rows_become_products_and_identify_their_lines(
    admin_client, admin, db_session
):
    loc = await _queue(admin_client, admin, db_session)
    vendor = loc["vendor"]["id"]
    rows = [
        {"vendor_id": vendor, "raw_text_norm": "WT BROCCOLI CROWNS", "name": "Broccoli crowns",
         "ingredient": {"name": "broccoli", "standard_key": "broccoli"}},
        # A second row naming the same new ingredient reuses it.
        {"vendor_id": vendor, "raw_text_norm": "RVRBND BREAD FLR 2KG",
         "name": "Riverbend bread flour", "ingredient": {"name": "bread flour"},
         "pack_qty": "2", "pack_unit": "kg"},
        {"vendor_id": vendor, "raw_text_norm": "BNLS CHKN BRST", "name": "Chicken breast",
         "ingredient": {"name": "bread flour"}},
    ]  # fmt: skip
    r = await admin_client.post("/api/v1/to-identify/name-products", json={"rows": rows})
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert [(x["applied"], x["error"]) for x in results] == [(2, None), (1, None), (1, None)]
    assert (await admin_client.get("/api/v1/to-identify")).json()["items"] == []
    flour = (await admin_client.get(f"/api/v1/products/{results[1]['product_id']}")).json()
    assert (flour["name"], Decimal(flour["pack_qty"]), flour["pack_unit"]) == (
        "Riverbend bread flour", Decimal("2"), "kg"
    )  # fmt: skip
    chicken = (await admin_client.get(f"/api/v1/products/{results[2]['product_id']}")).json()
    assert chicken["ingredient"]["id"] == flour["ingredient"]["id"]
    broccoli = (await admin_client.get(f"/api/v1/products/{results[0]['product_id']}")).json()
    assert broccoli["ingredient"]["name"] == "broccoli"
    # Each identified line emitted its price, and the wording is learned.
    obs = (
        await admin_client.get(
            "/api/v1/price-observations", params={"product_id": results[0]["product_id"]}
        )
    ).json()["items"]
    assert sorted(o["price"] for o in obs) == ["3.9000", "4.1200"]


async def test_a_failing_row_stands_alone(admin_client, admin, db_session):
    loc = await _queue(admin_client, admin, db_session)
    vendor = loc["vendor"]["id"]
    rows = [
        {"vendor_id": vendor, "raw_text_norm": "WT BROCCOLI CROWNS", "name": "Broccoli crowns",
         "ingredient_id": str(uuid.uuid4())},
        {"vendor_id": vendor, "raw_text_norm": "RVRBND BREAD FLR 2KG", "name": "Bread flour",
         "ingredient": {"name": "bread flour"}, "pack_qty": "2", "pack_unit": "furlong"},
        {"vendor_id": vendor, "raw_text_norm": "NO SUCH LINE", "name": "Nothing",
         "ingredient": {"name": "bread flour"}},
        {"vendor_id": vendor, "raw_text_norm": "BNLS CHKN BRST", "name": "Chicken breast",
         "ingredient": {"name": "chicken breast"}},
    ]  # fmt: skip
    r = await admin_client.post("/api/v1/to-identify/name-products", json={"rows": rows})
    assert r.status_code == 200, r.text
    results = r.json()["results"]
    assert [x["error"]["code"] if x["error"] else None for x in results] == [
        "not_found",
        "unknown_unit",
        "already_identified",
        None,
    ]
    assert [x["product_id"] is None for x in results] == [True, True, True, False]
    left = {
        g["raw_text_norm"] for g in (await admin_client.get("/api/v1/to-identify")).json()["items"]
    }
    assert left == {"WT BROCCOLI CROWNS", "RVRBND BREAD FLR 2KG"}
    # The failed flour row created no ingredient either.
    items = (
        await admin_client.get("/api/v1/ingredients/search", params={"q": "bread flour"})
    ).json()
    assert [i["name"] for i in items["items"] if i["kind"] == "ingredient"] == []


async def test_a_row_needs_exactly_one_ingredient(admin_client):
    r = await admin_client.post(
        "/api/v1/to-identify/name-products",
        json={"rows": [{"vendor_id": str(uuid.uuid4()), "raw_text_norm": "X", "name": "X"}]},
    )
    assert r.status_code == 422


async def test_a_row_naming_an_existing_product_offers_it_instead(admin_client, admin, db_session):
    loc = await _queue(admin_client, admin, db_session)
    vendor = loc["vendor"]["id"]
    flour = (await make_ingredient(admin_client, "bread flour"))["id"]
    existing = await admin_client.post(
        "/api/v1/products", json={"ingredient_id": flour, "name": "Riverbend Bread  Flour"}
    )
    assert existing.status_code == 201, existing.text
    row = {"vendor_id": vendor, "raw_text_norm": "RVRBND BREAD FLR 2KG",
           "name": "riverbend bread flour", "ingredient_id": flour}  # fmt: skip
    r = await admin_client.post("/api/v1/to-identify/name-products", json={"rows": [row]})
    [result] = r.json()["results"]
    assert result["product_id"] is None and result["error"]["code"] == "product_exists"
    assert result["error"]["product"]["id"] == existing.json()["id"]

    # "Use Riverbend Bread Flour": the lines go to the existing product, and none is created.
    r = await admin_client.post(
        "/api/v1/to-identify/name-products",
        json={"rows": [{**row, "product_id": existing.json()["id"]}]},
    )
    [result] = r.json()["results"]
    assert (result["product_id"], result["applied"], result["error"]) == (
        existing.json()["id"], 1, None
    )  # fmt: skip
    products = (await admin_client.get("/api/v1/products", params={"ingredient_id": flour})).json()
    assert len(products["items"]) == 1


async def test_marks_and_a_printed_size_do_not_hide_an_existing_product(
    admin_client, admin, db_session
):
    """Criterion 108: the naming pass compares by name key (2P)."""
    loc = await _queue(admin_client, admin, db_session)
    flour = (await make_ingredient(admin_client, "bread flour"))["id"]
    existing = await admin_client.post(
        "/api/v1/products",
        json={"ingredient_id": flour, "name": "Riverbend\u2122 Bread Flour, 2 kg"},
    )
    assert existing.status_code == 201, existing.text
    row = {"vendor_id": loc["vendor"]["id"], "raw_text_norm": "RVRBND BREAD FLR 2KG",
           "name": "riverbend bread flour", "ingredient_id": flour}  # fmt: skip
    r = await admin_client.post("/api/v1/to-identify/name-products", json={"rows": [row]})
    [result] = r.json()["results"]
    assert result["error"]["code"] == "product_exists"
    assert result["error"]["product"]["id"] == existing.json()["id"]


async def test_two_rows_naming_the_same_new_product_create_it_once(admin_client, admin, db_session):
    loc = await _queue(admin_client, admin, db_session)
    vendor = loc["vendor"]["id"]
    flour = (await make_ingredient(admin_client, "bread flour"))["id"]
    rows = [
        {"vendor_id": vendor, "raw_text_norm": "RVRBND BREAD FLR 2KG", "name": "Bread flour",
         "ingredient_id": flour},
        {"vendor_id": vendor, "raw_text_norm": "BNLS CHKN BRST", "name": "Bread Flour",
         "ingredient_id": flour},
    ]  # fmt: skip
    r = await admin_client.post("/api/v1/to-identify/name-products", json={"rows": rows})
    first, second = r.json()["results"]
    assert first["error"] is None
    assert second["error"]["code"] == "product_exists"
    assert second["error"]["product"]["id"] == first["product_id"]

    # Creating another anyway is still possible.
    r = await admin_client.post(
        "/api/v1/to-identify/name-products",
        json={"rows": [{**rows[1], "allow_duplicate": True}]},
    )
    [again] = r.json()["results"]
    assert again["error"] is None and again["product_id"] != first["product_id"]


async def test_an_inactive_product_cannot_be_used(admin_client, admin, db_session):
    loc = await _queue(admin_client, admin, db_session)
    flour = (await make_ingredient(admin_client, "bread flour"))["id"]
    gone = (
        await admin_client.post("/api/v1/products", json={"ingredient_id": flour, "name": "Old"})
    ).json()
    await admin_client.post(f"/api/v1/products/{gone['id']}/deactivate")
    row = {"vendor_id": loc["vendor"]["id"], "raw_text_norm": "BNLS CHKN BRST", "name": "Old",
           "product_id": gone["id"]}  # fmt: skip
    r = await admin_client.post("/api/v1/to-identify/name-products", json={"rows": [row]})
    [result] = r.json()["results"]
    assert result["error"]["code"] == "product_unavailable"
